#!/usr/bin/env python3
"""
PTS Runner for vkpeak-1.3.0

System Dependencies (from phoronix-test-suite info):
- Software Dependencies:
  * Vulkan runtime loader (libvulkan / vulkan-loader)
  * Vulkan-capable GPU with driver (NVIDIA, AMD, Intel, or software renderer)
- Estimated Install Time: <5 Seconds (pre-built binary)
- Environment Size: ~3 MB
- Test Type: Graphics (GPU Compute)
- Supported Platforms: Linux, Windows, MacOSX

Test Characteristics:
- Multi-threaded: GPU internal (Vulkan compute shaders)
- THFix_in_compile: N/A (pre-built binary, device index only)
- THChange_at_runtime: N/A (GPU thread count controlled by driver)
- Note: No CPU thread scaling. Runner executes as single run (thread_list=[vcpu_count]).
- RequiresDisplay: TRUE (Vulkan device enumeration may need display or ICD)
- Measures: FP16/FP32/FP64/INT16/INT32 scalar and vec4 GFLOPS via Vulkan compute
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from runner_common import detect_pts_failure_from_log, get_install_status, cleanup_pts_artifacts


class VkpeakRunner:
    def __init__(self, threads_arg=None, quick_mode=False):
        """
        Initialize vkpeak benchmark runner.

        Args:
            threads_arg: Ignored for GPU benchmarks (no CPU thread scaling).
            quick_mode: If True, run tests once (FORCE_TIMES_TO_RUN=1) for development.
        """
        self.benchmark = "vkpeak-1.3.0"
        self.benchmark_full = f"pts/{self.benchmark}"
        self.test_category = "FPU"
        self.test_category_dir = self.test_category.replace(" ", "_")

        # System info
        self.vcpu_count = os.cpu_count() or 1
        self.machine_name = os.environ.get('MACHINE_NAME', os.uname().nodename)
        self.os_name = self.get_os_name()

        # Standard 4-point scaling pattern (required by CODE_TEMPLATE)
        if threads_arg is None:
            n_4 = self.vcpu_count // 4
            self.thread_list = [n_4, n_4 * 2, n_4 * 3, self.vcpu_count]
            self.thread_list = sorted(list(set([t for t in self.thread_list if t > 0])))
            # GPU benchmark: thread count does not affect GPU execution; override to single run
            self.thread_list = [self.vcpu_count]
        else:
            self.thread_list = [min(threads_arg, self.vcpu_count)]

        # Project structure
        self.script_dir = Path(__file__).parent.resolve()
        self.project_root = self.script_dir.parent
        self.results_dir = self.project_root / "results" / self.machine_name / self.os_name / self.test_category_dir / self.benchmark

        self.quick_mode = quick_mode

        # Detect environment
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
            cmd = "lsb_release -d -s"
            result = subprocess.run(cmd.split(), capture_output=True, text=True)
            if result.returncode == 0:
                description = result.stdout.strip()
                parts = description.split()
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
        """Get current CPU frequencies for all CPUs (cross-platform)."""
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
        """Record current CPU frequencies to a file."""
        frequencies = self.get_cpu_frequencies()
        try:
            with open(output_file, 'w') as f:
                for freq in frequencies:
                    f.write(f"{freq}\n")
            return bool(frequencies)
        except Exception as e:
            print(f"  [WARN] Failed to write frequency file: {e}")
            return False

    def get_cpu_affinity_list(self, n):
        """Generate CPU affinity list. Not used for GPU benchmarks but required by CODE_TEMPLATE."""
        half = self.vcpu_count // 2
        cpu_list = []
        if n <= half:
            cpu_list = [str(i * 2) for i in range(n)]
        else:
            cpu_list = [str(i * 2) for i in range(half)]
            logical_count = n - half
            cpu_list.extend([str(i * 2 + 1) for i in range(logical_count)])
        return ','.join(cpu_list)

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
        except subprocess.TimeoutExpired:
            print("  [WARN] perf test timed out")
            return None
        except Exception:
            pass

        sw_events = "cpu-clock,task-clock,context-switches,cpu-migrations"
        try:
            result = subprocess.run(
                ['bash', '-c', f"{perf_path} stat -e {sw_events} sleep 0.01 2>&1"],
                capture_output=True, text=True, timeout=3
            )
            if result.returncode == 0:
                print(f"  [INFO] Hardware PMU not available. Using software events: {sw_events}")
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
                print(f"  [WARN] perf_event_paranoid={current_value} is too restrictive")
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
        installed_dir = Path.home() / '.phoronix-test-suite' / 'installed-tests' / 'pts' / self.benchmark
        if installed_dir.exists():
            print(f"  [CLEAN] Removing installed test: {installed_dir}")
            shutil.rmtree(installed_dir)
        print("  [OK] PTS cache cleaned")

    def install_benchmark(self):
        """Install vkpeak. Pre-built binary, no compilation needed."""
        print(f"\n>>> Installing {self.benchmark_full}...")

        print("  [INFO] Removing existing installation...")
        remove_cmd = f'echo "y" | phoronix-test-suite remove-installed-test "{self.benchmark_full}"'
        subprocess.run(['bash', '-c', remove_cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

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
        Run vkpeak benchmark.
        num_threads parameter is kept for framework compatibility but has no effect
        on GPU execution (no CPU thread scaling for Vulkan compute benchmarks).
        """
        self.results_dir.mkdir(parents=True, exist_ok=True)
        log_file = self.results_dir / f"{num_threads}-thread.log"
        stdout_log = self.results_dir / "stdout.log"
        perf_stats_file = self.results_dir / f"{num_threads}-thread_perf_stats.txt"
        freq_start_file = self.results_dir / f"{num_threads}-thread_freq_start.txt"
        freq_end_file = self.results_dir / f"{num_threads}-thread_freq_end.txt"
        perf_summary_file = self.results_dir / f"{num_threads}-thread_perf_summary.json"

        # GPU benchmark: no taskset, no NUM_CPU_CORES
        pts_base_cmd = f'phoronix-test-suite batch-run {self.benchmark_full}'

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
            else:
                perf_cmd = f"perf stat -e {self.perf_events} -o {perf_stats_file}"
            pts_cmd = f'{batch_env} {perf_cmd} {pts_base_cmd}'
        else:
            pts_cmd = f'{batch_env} {pts_base_cmd}'

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
                    perf_summary = {'perf_stats_file': str(perf_stats_file), 'cpu_list': 'all'}
                    with open(perf_summary_file, 'w') as f:
                        json.dump(perf_summary, f, indent=2)
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
            f.write(f"{'Test':<40} {'Value':>15} {'Unit':<10}\n")
            f.write("-"*70 + "\n")
            for result in all_results:
                val_str = f"{result['value']:.4f}" if result['value'] is not None else "FAILED"
                desc = result.get('description') or result.get('test_name') or ''
                f.write(f"{str(desc):<40} {val_str:>15} {str(result['unit'] or ''):<10}\n")

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
        print(f"Thread List: {self.thread_list} (GPU benchmark: CPU thread count not applicable)")
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
            print(f"  [INFO] Cleaned existing {prefix} results")

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
        description='PTS Runner for vkpeak-1.3.0 (GPU Vulkan compute benchmark)',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        'threads_pos',
        nargs='?',
        type=int,
        help='Thread count (optional positional; ignored for GPU benchmark)'
    )
    parser.add_argument(
        '--threads', type=int, default=None,
        help='Thread count (ignored for GPU benchmark; kept for CLI compatibility)'
    )
    parser.add_argument(
        '--quick', action='store_true',
        help='Quick mode: run each test once (FORCE_TIMES_TO_RUN=1)'
    )
    args = parser.parse_args()

    # Resolve threads argument (--threads takes priority over positional)
    threads = args.threads if args.threads is not None else args.threads_pos

    runner = VkpeakRunner(threads_arg=threads, quick_mode=args.quick)
    success = runner.run()
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
