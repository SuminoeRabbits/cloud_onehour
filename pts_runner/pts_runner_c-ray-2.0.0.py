#!/usr/bin/env python3
"""
PTS Runner for c-ray-2.0.0

System Dependencies (from phoronix-test-suite info):
- Software Dependencies:
  * C/C++ Compiler Toolchain (build-utilities: gcc, make)
- Estimated Install Time: ~5 Seconds
- Environment Size: ~6 MB
- Test Type: Processor
- Supported Platforms: Linux, Solaris, MacOSX, BSD

Test Characteristics:
- Multi-threaded: Yes (c-ray-fast uses pthreads, auto-detects available CPUs)
- THFix_in_compile: false - Thread count NOT fixed at compile time
- THChange_at_runtime: false - c-ray-fast auto-detects CPU count at runtime;
    no explicit -t flag passed by PTS; taskset is used to limit available CPUs.
- Note: Thread scaling is achieved via taskset CPU affinity (limits visible CPUs).
- Measures: Total rendering time in Seconds (lower is better)
- Default test: 4K (3840x2160), 16 Rays Per Pixel
- SMP tag: renders in parallel across all available logical CPUs

Build dependencies by OS:
  Ubuntu/Debian    : build-essential (gcc, make) - covered by setup_pts.sh
  RHEL/OracleLinux : Development Tools (gcc, make) - covered by setup_pts.sh
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from runner_common import detect_pts_failure_from_log, get_install_status, cleanup_pts_artifacts


class CRayRunner:
    def __init__(self, threads_arg=None, quick_mode=False):
        """
        Initialize C-Ray benchmark runner.

        Args:
            threads_arg: Fixed thread count. None = 4-point scaling [n/4, n/2, 3n/4, n].
            quick_mode: If True, run each test once (FORCE_TIMES_TO_RUN=1) for development.
        """
        self.benchmark = "c-ray-2.0.0"
        self.benchmark_full = f"pts/{self.benchmark}"
        self.test_category = "FPU"
        self.test_category_dir = self.test_category.replace(" ", "_")

        # System info
        self.vcpu_count = os.cpu_count() or 1
        self.machine_name = os.environ.get('MACHINE_NAME', os.uname().nodename)
        self.os_name = self.get_os_name()

        # 4-point thread scaling: [n/4, n/2, 3n/4, n]
        if threads_arg is None:
            n_4 = self.vcpu_count // 4
            self.thread_list = [n_4, n_4 * 2, n_4 * 3, self.vcpu_count]
            self.thread_list = sorted(list(set([t for t in self.thread_list if t > 0])))
        else:
            self.thread_list = [min(threads_arg, self.vcpu_count)]

        # Project structure
        self.script_dir = Path(__file__).parent.resolve()
        self.project_root = self.script_dir.parent
        self.results_dir = self.project_root / "results" / self.machine_name / self.os_name / self.test_category_dir / self.benchmark

        self.quick_mode = quick_mode

        self.is_wsl_env = self.is_wsl()
        if self.is_wsl_env:
            print("  [INFO] Running on WSL environment")

        self.ensure_upload_disabled()

        # CRITICAL: Setup perf permissions BEFORE testing perf availability
        self.perf_paranoid = self.check_and_setup_perf_permissions()
        self.perf_events = self.get_perf_events()
        if self.perf_events:
            print(f"  [OK] Perf monitoring enabled with events: {self.perf_events}")
        else:
            print("  [INFO] Perf monitoring disabled (command missing or unsupported)")

    def get_os_name(self):
        """Get OS name and version formatted as <Distro>_<Version>."""
        try:
            result = subprocess.run("lsb_release -d -s".split(), capture_output=True, text=True)
            if result.returncode == 0:
                parts = result.stdout.strip().split()
                if len(parts) >= 2:
                    return f"{parts[0]}_{parts[1].replace('.', '_')}"
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
                return f"{info['NAME'].split()[0]}_{info['VERSION_ID'].replace('.', '_')}"
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
        """Get current CPU frequencies (cross-platform)."""
        frequencies = []

        try:
            result = subprocess.run(
                ['bash', '-c', 'grep "cpu MHz" /proc/cpuinfo'],
                capture_output=True, text=True
            )
            if result.returncode == 0 and result.stdout.strip():
                for line in result.stdout.strip().split('\n'):
                    parts = line.split(':')
                    if len(parts) >= 2:
                        frequencies.append(int(float(parts[1].strip()) * 1000))
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
                        frequencies.append(int(f.read().strip()))
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
        """Record CPU frequencies to file."""
        frequencies = self.get_cpu_frequencies()
        try:
            with open(output_file, 'w') as f:
                for freq in frequencies:
                    f.write(f"{freq}\n")
            return bool(frequencies)
        except Exception as e:
            print(f"  [WARN] Failed to write frequency file: {e}")
            return False

    def get_perf_events(self):
        """Determine available perf events (3-stage fallback)."""
        perf_path = shutil.which("perf")
        if not perf_path:
            print("  [INFO] perf command not found in PATH")
            return None

        hw_events = "cycles,instructions,cpu-clock,task-clock,context-switches,cpu-migrations"
        try:
            result = subprocess.run(
                ['bash', '-c', f"{perf_path} stat -e {hw_events} sleep 0.01 2>&1"],
                capture_output=True, text=True, timeout=3
            )
            output = result.stdout + result.stderr
            if result.returncode == 0 and '<not supported>' not in output:
                print(f"  [OK] Hardware PMU available: {hw_events}")
                return hw_events
        except (subprocess.TimeoutExpired, Exception):
            pass

        sw_events = "cpu-clock,task-clock,context-switches,cpu-migrations"
        try:
            result = subprocess.run(
                ['bash', '-c', f"{perf_path} stat -e {sw_events} sleep 0.01 2>&1"],
                capture_output=True, text=True, timeout=3
            )
            if result.returncode == 0:
                print(f"  [INFO] Using software events: {sw_events}")
                return sw_events
        except Exception:
            pass

        print("  [INFO] perf command exists but is not functional")
        return None

    def check_and_setup_perf_permissions(self):
        """Check perf_event_paranoid and adjust if needed."""
        print(f"\n{'='*80}")
        print(">>> Checking perf_event_paranoid setting")
        print(f"{'='*80}")

        try:
            result = subprocess.run(
                ['cat', '/proc/sys/kernel/perf_event_paranoid'],
                capture_output=True, text=True, check=True
            )
            current_value = int(result.stdout.strip())
            print(f"  [INFO] Current perf_event_paranoid: {current_value}")

            if current_value >= 1:
                result = subprocess.run(
                    ['sudo', 'sysctl', '-w', 'kernel.perf_event_paranoid=0'],
                    capture_output=True, text=True
                )
                if result.returncode == 0:
                    print("  [OK] perf_event_paranoid adjusted to 0")
                    return 0
                else:
                    print("  [ERROR] Failed to adjust perf_event_paranoid")
                    return current_value
            else:
                print(f"  [OK] perf_event_paranoid={current_value} is acceptable")
                return current_value
        except Exception as e:
            print(f"  [ERROR] Could not check perf_event_paranoid: {e}")
            return 2

    def ensure_upload_disabled(self):
        """Ensure PTS results upload is disabled."""
        config_path = Path.home() / ".phoronix-test-suite" / "user-config.xml"
        if not config_path.exists():
            return
        try:
            with open(config_path, 'r') as f:
                content = f.read()
            if '<UploadResults>TRUE</UploadResults>' in content:
                print("  [WARN] UploadResults is TRUE. Disabling...")
                content = content.replace('<UploadResults>TRUE</UploadResults>', '<UploadResults>FALSE</UploadResults>')
                with open(config_path, 'w') as f:
                    f.write(content)
                print("  [OK] UploadResults set to FALSE")
        except Exception as e:
            print(f"  [WARN] Failed to update user-config.xml: {e}")

    def clean_pts_cache(self):
        """Clean PTS installed tests for fresh installation."""
        installed_dir = Path.home() / '.phoronix-test-suite' / 'installed-tests' / 'pts' / self.benchmark
        if installed_dir.exists():
            print(f"  [CLEAN] Removing installed test: {installed_dir}")
            shutil.rmtree(installed_dir)
        print("  [OK] PTS cache cleaned")

    def get_cpu_affinity_list(self, n):
        """
        Generate CPU affinity list for HyperThreading optimization.
        Prioritizes physical cores (even IDs) first, then logical cores (odd IDs).
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

    def install_benchmark(self):
        """
        Install c-ray-2.0.0 from source.
        Compiles c-ray-fast (multi-threaded) using system gcc/make.
        No special compiler flags needed; simple Makefile-based build.
        """
        print(f"\n>>> Installing {self.benchmark_full}...")

        print("  [INFO] Removing existing installation...")
        remove_cmd = f'echo "y" | phoronix-test-suite remove-installed-test "{self.benchmark_full}"'
        subprocess.run(['bash', '-c', remove_cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        nproc = os.cpu_count() or 1
        # c-ray uses a simple Makefile. NUM_CPU_CORES is NOT used at compile time.
        install_cmd = (
            f'MAKEFLAGS="-j{nproc}" '
            f'phoronix-test-suite batch-install {self.benchmark_full}'
        )

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
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1
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
        log_file = install_log
        pts_test_failed, pts_failure_reason = detect_pts_failure_from_log(log_file)
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

        if install_failed:
            print(f"\n  [ERROR] Installation failed (returncode={returncode})")
            if pts_test_failed:
                print(f"  [ERROR] PTS failure: {pts_failure_reason}")
            sys.exit(1)

        installed_dir = Path.home() / '.phoronix-test-suite' / 'installed-tests' / 'pts' / self.benchmark
        if not installed_dir.exists():
            print(f"  [ERROR] Installation directory not found: {installed_dir}")
            sys.exit(1)

        print(f"  [OK] Installation completed: {installed_dir}")

    def run_benchmark(self, num_threads):
        """
        Run c-ray benchmark with taskset to limit CPU affinity for thread scaling.

        c-ray-fast auto-detects available CPUs. taskset restricts visible CPUs
        to achieve the target thread count without recompilation.
        """
        self.results_dir.mkdir(parents=True, exist_ok=True)
        log_file = self.results_dir / f"{num_threads}-thread.log"
        stdout_log = self.results_dir / "stdout.log"
        perf_stats_file = self.results_dir / f"{num_threads}-thread_perf_stats.txt"
        freq_start_file = self.results_dir / f"{num_threads}-thread_freq_start.txt"
        freq_end_file = self.results_dir / f"{num_threads}-thread_freq_end.txt"
        perf_summary_file = self.results_dir / f"{num_threads}-thread_perf_summary.json"

        # taskset to limit visible CPUs; c-ray-fast auto-detects available cores
        if num_threads >= self.vcpu_count:
            cpu_list = ','.join([str(i) for i in range(self.vcpu_count)])
            pts_base_cmd = f'phoronix-test-suite batch-run {self.benchmark_full}'
        else:
            cpu_list = self.get_cpu_affinity_list(num_threads)
            pts_base_cmd = f'taskset -c {cpu_list} phoronix-test-suite batch-run {self.benchmark_full}'

        sanitized_benchmark = self.benchmark.replace('.', '')
        for cmd in [
            f'phoronix-test-suite remove-result {self.benchmark}-{num_threads}threads',
            f'phoronix-test-suite remove-result {sanitized_benchmark}-{num_threads}threads',
        ]:
            subprocess.run(['bash', '-c', cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        quick_env = 'FORCE_TIMES_TO_RUN=1 ' if self.quick_mode else ''
        batch_env = (
            f'{quick_env}BATCH_MODE=1 SKIP_ALL_PROMPTS=1 DISPLAY_COMPACT_RESULTS=1 '
            f'TEST_RESULTS_NAME={self.benchmark}-{num_threads}threads '
            f'TEST_RESULTS_IDENTIFIER={self.benchmark}-{num_threads}threads '
            f'TEST_RESULTS_DESCRIPTION={self.benchmark}-{num_threads}threads'
        )

        if self.perf_events:
            if self.perf_paranoid <= 0:
                perf_cmd = f"perf stat -e {self.perf_events} -A -a -o {perf_stats_file}"
                print("  [INFO] Running with perf monitoring (per-CPU mode)")
            else:
                perf_cmd = f"perf stat -e {self.perf_events} -o {perf_stats_file}"
                print("  [INFO] Running with perf monitoring (aggregated mode)")
            pts_cmd = f'{batch_env} {perf_cmd} {pts_base_cmd}'
        else:
            pts_cmd = f'{batch_env} {pts_base_cmd}'
            print("  [INFO] Running without perf")

        print(f"  [INFO] CPU affinity: {cpu_list} ({num_threads} threads via taskset)")

        print("  [INFO] Recording CPU frequency before benchmark...")
        self.record_cpu_frequency(freq_start_file)

        with open(log_file, 'w') as log_f, open(stdout_log, 'a') as stdout_f:
            stdout_f.write(f"\n{'='*80}\n")
            stdout_f.write(f"[PTS BENCHMARK COMMAND - {num_threads} thread(s)]\n")
            stdout_f.write(f"{pts_cmd}\n")
            stdout_f.write(f"{'='*80}\n\n")
            stdout_f.flush()

            process = subprocess.Popen(
                ['bash', '-c', pts_cmd],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1
            )
            for line in process.stdout:
                print(line, end='')
                log_f.write(line)
                stdout_f.write(line)
                log_f.flush()
                stdout_f.flush()
            process.wait()
            returncode = process.returncode

        self.record_cpu_frequency(freq_end_file)

        pts_test_failed, pts_failure_reason = detect_pts_failure_from_log(log_file)

        if returncode == 0 and not pts_test_failed:
            print("\n[OK] Benchmark completed successfully")
            if self.perf_events and perf_stats_file.exists():
                try:
                    with open(perf_summary_file, 'w') as f:
                        json.dump({'perf_stats_file': str(perf_stats_file), 'cpu_list': cpu_list}, f, indent=2)
                except Exception as e:
                    print(f"  [WARN] Failed to save perf summary: {e}")
            return True
        else:
            reason = pts_failure_reason if pts_test_failed else f"returncode={returncode}"
            print(f"\n[ERROR] Benchmark failed: {reason}")
            return False

    def export_results(self):
        """Export benchmark results to CSV and JSON formats."""
        print(f"\n{'='*80}")
        print(">>> Exporting results")
        print(f"{'='*80}")

        pts_results_dir = Path.home() / ".phoronix-test-suite" / "test-results"

        for num_threads in self.thread_list:
            result_name = f"{self.benchmark}-{num_threads}threads"
            result_dir_name = result_name.replace('.', '')
            result_dir = pts_results_dir / result_dir_name

            if not result_dir.exists():
                print(f"  [WARN] Result not found: {result_dir}")
                continue

            csv_output = self.results_dir / f"{num_threads}-thread.csv"
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

            json_output = self.results_dir / f"{num_threads}-thread.json"
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
                try:
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
                except Exception as e:
                    print(f"  [WARN] Failed to parse {json_file}: {e}")

        if not all_results:
            print("[WARN] No results found for summary generation")
            return

        with open(summary_log, 'w') as f:
            f.write("="*80 + "\n")
            f.write(f"Benchmark Summary: {self.benchmark}\n")
            f.write(f"Machine: {self.machine_name}\n")
            f.write(f"Test Category: {self.test_category}\n")
            f.write("="*80 + "\n\n")
            f.write(f"{'Threads':<10} {'Test':<35} {'Value':>12} {'Unit':<10}\n")
            f.write("-"*70 + "\n")
            for result in all_results:
                val_str = f"{result['value']:.2f}" if result['value'] is not None else "FAILED    "
                desc = result.get('description') or result.get('test_name') or ''
                f.write(
                    f"{result['threads']:<10} {str(desc):<35} {val_str:>12} "
                    f"{str(result['unit'] or ''):<10}\n"
                )

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
            self.install_benchmark()
        else:
            print(f"[INFO] Benchmark already installed, skipping installation: {self.benchmark_full}")

        failed = []
        for num_threads in self.thread_list:
            if not self.run_benchmark(num_threads):
                failed.append(num_threads)

        self.export_results()
        self.generate_summary()

        cleanup_pts_artifacts(self.benchmark)

        # ── Runner Output Protocol (mandatory) ───────────────────────────────────
        print(f"\n{'='*80}")
        print("Benchmark Summary")
        print(f"{'='*80}")
        print(f"Total tests:  {len(self.thread_list)}")
        print(f"Successful:   {len(self.thread_list) - len(failed)}")
        print(f"Failed:       {len(failed)}")
        if failed:
            print(f"Failed thread counts: {failed}")
        print(f"{'='*80}")
        # ─────────────────────────────────────────────────────────────────────────

        return len(failed) == 0


def main():
    parser = argparse.ArgumentParser(
        description='PTS Runner for c-ray-2.0.0 (multi-threaded CPU raytracer)',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        'threads_pos',
        nargs='?',
        type=int,
        help='Number of threads (optional positional, omit for 4-point scaling mode)'
    )
    parser.add_argument(
        '--threads', type=int, default=None,
        help='Fixed thread count. Default: 4-point scaling [n/4, n/2, 3n/4, n]'
    )
    parser.add_argument(
        '--quick', action='store_true',
        help='Quick mode: run each test once (FORCE_TIMES_TO_RUN=1)'
    )
    args = parser.parse_args()

    # Resolve threads argument (--threads takes priority over positional)
    threads = args.threads if args.threads is not None else args.threads_pos

    runner = CRayRunner(threads_arg=threads, quick_mode=args.quick)
    success = runner.run()
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
