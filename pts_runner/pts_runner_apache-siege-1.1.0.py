#!/usr/bin/env python3
"""
PTS Runner for apache-siege-1.1.0

System Dependencies (from phoronix-test-suite info):
- Software Dependencies:
  * C/C++ Compiler Toolchain
  * PERL
  * Perl Compatible Regular Expressions
  * OpenSSL Development Files
  * Expat XML Parser Library
- Estimated Install Time: 143 Seconds
- Environment Size: 208 MB
- Test Type: System
- Supported Platforms: Linux, Solaris, BSD, MacOSX

Test Characteristics:
- Multi-threaded: Yes (Apache HTTPD + Siege workload is SMP-capable)
- THFix_in_compile: false
- THChange_at_runtime: true
  Runtime scaling is controlled by limiting CPU affinity for the benchmark
  process tree. The upstream test options keep six concurrent-user cases:
  10 / 50 / 100 / 200 / 500 / 1000.

Result Scale: Transactions Per Second (Higher Is Better)
TimesToRun  : 3
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from runner_common import cleanup_pts_artifacts, detect_pts_failure_from_log, get_install_status


class PreSeedDownloader:
    """Pre-download large test files into PTS download cache using aria2c."""

    def __init__(self, cache_dir=None):
        if cache_dir:
            self.cache_dir = Path(cache_dir)
        else:
            self.cache_dir = Path.home() / ".phoronix-test-suite" / "download-cache"
        self.aria2_available = shutil.which("aria2c") is not None

    def is_aria2_available(self):
        return self.aria2_available

    def download_from_xml(self, benchmark_name, threshold_mb=96):
        """Parse downloads.xml and accelerate large files with aria2c."""
        if not self.aria2_available:
            return False

        profile_path = Path.home() / ".phoronix-test-suite" / "test-profiles" / benchmark_name / "downloads.xml"
        if not profile_path.exists():
            print(f"  [WARN] downloads.xml not found at {profile_path}")
            print(f"  [INFO] Fetching test profile via phoronix-test-suite info {benchmark_name}...")
            try:
                subprocess.run(
                    ["phoronix-test-suite", "info", benchmark_name],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception as e:
                print(f"  [WARN] Failed to run phoronix-test-suite info: {e}")
                return False
            if not profile_path.exists():
                print(f"  [WARN] downloads.xml still missing after info: {profile_path}")
                return False

        try:
            tree = ET.parse(profile_path)
            root = tree.getroot()
            downloads_node = root.find("Downloads")
            if downloads_node is None:
                return False

            for package in downloads_node.findall("Package"):
                url_node = package.find("URL")
                filename_node = package.find("FileName")
                filesize_node = package.find("FileSize")
                if url_node is None or filename_node is None:
                    continue

                urls = [u.strip() for u in url_node.text.split(",")]
                url = urls[0] if urls else None
                filename = filename_node.text.strip()
                if not url:
                    continue

                size_bytes = -1
                if filesize_node is not None and filesize_node.text:
                    try:
                        size_bytes = int(filesize_node.text.strip())
                    except ValueError:
                        pass
                if size_bytes <= 0:
                    size_bytes = self.get_remote_file_size(url)
                if size_bytes > 0 and size_bytes / (1024 * 1024) >= threshold_mb:
                    print(f"  [INFO] {filename} is large ({size_bytes / (1024 * 1024):.1f} MB), accelerating with aria2c...")
                    self.ensure_file(urls, filename, size_bytes=size_bytes)
        except Exception as e:
            print(f"  [ERROR] Failed to parse downloads.xml: {e}")
            return False
        return True

    def get_remote_file_size(self, url):
        try:
            result = subprocess.run(["curl", "-s", "-I", "-L", url], capture_output=True, text=True)
            if result.returncode != 0:
                return -1
            for line in result.stdout.splitlines():
                if line.lower().startswith("content-length:"):
                    return int(line.split(":", 1)[1].strip())
        except Exception:
            pass
        return -1

    def ensure_file(self, urls, filename, size_bytes=-1):
        target_path = self.cache_dir / filename
        if target_path.exists():
            if size_bytes > 0 and target_path.stat().st_size != size_bytes:
                print(f"  [CACHE] Size mismatch for {filename}, re-downloading...")
            else:
                print(f"  [CACHE] File found: {filename}")
                return True

        if isinstance(urls, str):
            urls = [urls]

        large_threshold = 10 * 1024 * 1024 * 1024
        num_conn = "4" if size_bytes >= large_threshold else "16"
        print(f"  [ARIA2] Downloading {filename}...")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            "aria2c",
            f"-x{num_conn}",
            f"-s{num_conn}",
            "--connect-timeout=30",
            "--timeout=120",
            "--max-tries=2",
            "--retry-wait=5",
            "--continue=true",
            "-d",
            str(self.cache_dir),
            "-o",
            filename,
        ] + urls
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


class ApacheSiegeRunner:
    def __init__(self, threads_arg=None, quick_mode=False):
        self.benchmark = "apache-siege-1.1.0"
        self.benchmark_full = f"pts/{self.benchmark}"
        self.test_category = "Network"
        self.test_category_dir = self.test_category.replace(" ", "_")

        self.vcpu_count = os.cpu_count() or 1
        self.machine_name = os.environ.get("MACHINE_NAME", os.uname().nodename)
        self.os_name = self.get_os_name()

        if threads_arg is None:
            n_4 = self.vcpu_count // 4
            self.thread_list = [n_4, n_4 * 2, n_4 * 3, self.vcpu_count]
            self.thread_list = sorted(list(set([t for t in self.thread_list if t > 0])))
        else:
            n = min(threads_arg, self.vcpu_count)
            self.thread_list = [n]

        self.script_dir = Path(__file__).parent.resolve()
        self.project_root = self.script_dir.parent
        self.results_dir = self.project_root / "results" / self.machine_name / self.os_name / self.test_category_dir / self.benchmark

        self.quick_mode = quick_mode
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
        try:
            result = subprocess.run(["lsb_release", "-d", "-s"], capture_output=True, text=True)
            if result.returncode == 0:
                parts = result.stdout.strip().split()
                if len(parts) >= 2:
                    return f"{parts[0]}_{parts[1].replace('.', '_')}"
        except Exception:
            pass
        try:
            info = {}
            with open("/etc/os-release", "r") as handle:
                for line in handle:
                    if "=" in line:
                        key, value = line.strip().split("=", 1)
                        info[key] = value.strip('"')
            if "NAME" in info and "VERSION_ID" in info:
                return f"{info['NAME'].split()[0]}_{info['VERSION_ID'].replace('.', '_')}"
        except Exception:
            pass
        return "Unknown_OS"

    def is_wsl(self):
        try:
            with open("/proc/version", "r") as handle:
                content = handle.read().lower()
            return "microsoft" in content or "wsl" in content
        except Exception:
            return False

    def get_cpu_affinity_list(self, n):
        half = self.vcpu_count // 2
        if n <= half:
            cpu_list = [str(i * 2) for i in range(n)]
        else:
            cpu_list = [str(i * 2) for i in range(half)]
            logical_count = n - half
            cpu_list.extend([str(i * 2 + 1) for i in range(logical_count)])
        return ",".join(cpu_list)

    def get_cpu_frequencies(self):
        frequencies = []
        try:
            result = subprocess.run(["bash", "-c", 'grep "cpu MHz" /proc/cpuinfo'], capture_output=True, text=True)
            if result.returncode == 0 and result.stdout.strip():
                for line in result.stdout.strip().split("\n"):
                    parts = line.split(":")
                    if len(parts) >= 2:
                        frequencies.append(int(float(parts[1].strip()) * 1000))
                if frequencies:
                    return frequencies
        except Exception:
            pass
        try:
            freq_files = sorted(Path("/sys/devices/system/cpu").glob("cpu[0-9]*/cpufreq/scaling_cur_freq"))
            if not freq_files:
                freq_files = sorted(Path("/sys/devices/system/cpu").glob("cpu[0-9]*/cpufreq/cpuinfo_cur_freq"))
            for freq_file in freq_files:
                try:
                    frequencies.append(int(freq_file.read_text().strip()))
                except Exception:
                    frequencies.append(0)
            if frequencies:
                return frequencies
        except Exception:
            pass
        try:
            result = subprocess.run(["lscpu"], capture_output=True, text=True)
            if result.returncode == 0:
                for line in result.stdout.split("\n"):
                    if "CPU MHz" in line or "CPU max MHz" in line:
                        parts = line.split(":")
                        if len(parts) >= 2:
                            return [int(float(parts[1].strip().replace(",", ".")) * 1000)] * self.vcpu_count
        except Exception:
            pass
        return frequencies

    def record_cpu_frequency(self, output_file):
        frequencies = self.get_cpu_frequencies()
        if frequencies:
            try:
                with open(output_file, "w") as handle:
                    for freq in frequencies:
                        handle.write(f"{freq}\n")
                return True
            except Exception as e:
                print(f"  [WARN] Failed to write frequency file: {e}")
                return False
        try:
            output_file.touch()
        except Exception:
            pass
        return False

    def parse_perf_stats(self, perf_stats_file, cpu_list):
        per_cpu_metrics = {}
        try:
            with open(perf_stats_file, "r") as handle:
                for line in handle:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split()
                    if len(parts) >= 3:
                        try:
                            cpu_num = parts[0].replace("CPU", "")
                            value = float(parts[1].replace(",", ""))
                            event = parts[2]
                            per_cpu_metrics.setdefault(cpu_num, {})[event] = value
                        except ValueError:
                            continue
        except FileNotFoundError:
            print(f"  [INFO] Perf stats not found: {perf_stats_file} (likely disabled or missing)")
        return {"per_cpu_metrics": per_cpu_metrics, "cpu_list": cpu_list}

    def install_benchmark(self):
        print(f"\n{'=' * 80}")
        print(f">>> Installing {self.benchmark_full}")
        print(f"{'=' * 80}")

        downloader = PreSeedDownloader()
        if downloader.is_aria2_available():
            print("  [OK] aria2c is available for accelerated downloads")
            downloader.download_from_xml(self.benchmark_full, threshold_mb=96)
        else:
            print("  [INFO] aria2c not available, using PTS default downloader")

        remove_cmd = f'echo "y" | phoronix-test-suite remove-installed-test "{self.benchmark_full}"'
        subprocess.run(["bash", "-c", remove_cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        nproc = os.cpu_count() or 1
        install_cmd = f'MAKEFLAGS="-j{nproc}" phoronix-test-suite batch-install {self.benchmark_full}'

        print("  Running installation...")
        install_log_env = os.environ.get("PTS_INSTALL_LOG", "").strip().lower()
        install_log_path = os.environ.get("PTS_INSTALL_LOG_PATH", "").strip()
        use_install_log = install_log_env in {"1", "true", "yes"} or bool(install_log_path)
        install_log = Path(install_log_path) if install_log_path else (self.results_dir / "install.log")
        log_file = install_log
        log_f = open(install_log, "w") if use_install_log else None
        if log_f:
            log_f.write(f"[PTS INSTALL COMMAND]\n{install_cmd}\n\n")
            log_f.flush()

        process = subprocess.Popen(
            ["bash", "-c", install_cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        install_output = []
        for line in process.stdout:
            print(line, end="")
            if log_f:
                log_f.write(line)
                log_f.flush()
            install_output.append(line)
        process.wait()
        returncode = process.returncode
        pts_test_failed, pts_failure_reason = detect_pts_failure_from_log(log_file)
        if log_f:
            log_f.close()

        full_output = "".join(install_output)
        install_failed = False
        if returncode != 0:
            install_failed = True
        elif pts_test_failed:
            install_failed = True
        elif "Checksum Failed" in full_output or "Downloading of needed test files failed" in full_output:
            install_failed = True
        elif "ERROR" in full_output or "FAILED" in full_output:
            install_failed = True

        if install_failed:
            print(f"\n  [ERROR] Installation failed with return code {returncode}")
            if pts_failure_reason:
                print(f"  [INFO] Reason: {pts_failure_reason}")
            if use_install_log:
                print(f"  [INFO] Install log: {install_log}")
            sys.exit(1)

        install_dir = Path.home() / ".phoronix-test-suite" / "installed-tests" / "pts" / self.benchmark
        if not install_dir.exists():
            print(f"  [ERROR] Installation verification failed: {install_dir} not found")
            sys.exit(1)

        verify_result = subprocess.run(
            ["bash", "-c", f"phoronix-test-suite test-installed {self.benchmark_full}"],
            capture_output=True,
            text=True,
        )
        if verify_result.returncode != 0:
            print("  [WARN] test-installed check failed, but directory exists — continuing")

        print(f"  [OK] Installation completed and verified: {install_dir}")

    def run_benchmark(self, num_threads):
        self.results_dir.mkdir(parents=True, exist_ok=True)
        log_file = self.results_dir / f"{num_threads}-thread.log"
        stdout_log = self.results_dir / "stdout.log"
        perf_stats_file = self.results_dir / f"{num_threads}-thread_perf_stats.txt"
        freq_start_file = self.results_dir / f"{num_threads}-thread_freq_start.txt"
        freq_end_file = self.results_dir / f"{num_threads}-thread_freq_end.txt"
        perf_summary_file = self.results_dir / f"{num_threads}-thread_perf_summary.json"

        if num_threads >= self.vcpu_count:
            cpu_list = ",".join(str(i) for i in range(self.vcpu_count))
            pts_base_cmd = f"phoronix-test-suite batch-run {self.benchmark_full}"
        else:
            cpu_list = self.get_cpu_affinity_list(num_threads)
            pts_base_cmd = f"taskset -c {cpu_list} phoronix-test-suite batch-run {self.benchmark_full}"

        quick_env = "FORCE_TIMES_TO_RUN=1 " if self.quick_mode else ""
        sanitized_benchmark = self.benchmark.replace(".", "")
        for cmd in [
            f"phoronix-test-suite remove-result {self.benchmark}-{num_threads}threads",
            f"phoronix-test-suite remove-result {sanitized_benchmark}-{num_threads}threads",
        ]:
            subprocess.run(["bash", "-c", cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        batch_env = (
            f"{quick_env}BATCH_MODE=1 SKIP_ALL_PROMPTS=1 DISPLAY_COMPACT_RESULTS=1 "
            f"TEST_RESULTS_NAME={self.benchmark}-{num_threads}threads "
            f"TEST_RESULTS_IDENTIFIER={self.benchmark}-{num_threads}threads "
            f"TEST_RESULTS_DESCRIPTION={self.benchmark}-{num_threads}threads"
        )

        if self.perf_events:
            if self.perf_paranoid <= 0:
                perf_cmd = f"perf stat -e {self.perf_events} -A -a -o {perf_stats_file}"
                print("  [INFO] Running with perf monitoring (per-CPU mode)")
            else:
                perf_cmd = f"perf stat -e {self.perf_events} -o {perf_stats_file}"
                print("  [INFO] Running with perf monitoring (aggregated mode)")
            pts_cmd = f"{batch_env} {perf_cmd} {pts_base_cmd}"
        else:
            pts_cmd = f"{batch_env} {pts_base_cmd}"
            print("  [INFO] Running without perf")

        print("[INFO] Recording CPU frequency before benchmark...")
        self.record_cpu_frequency(freq_start_file)

        with open(log_file, "w") as log_f, open(stdout_log, "a") as stdout_f:
            stdout_f.write(f"\n{'=' * 80}\n")
            stdout_f.write(f"[PTS BENCHMARK COMMAND - {num_threads} thread(s)]\n")
            stdout_f.write(f"{pts_cmd}\n")
            stdout_f.write(f"{'=' * 80}\n\n")
            stdout_f.flush()

            process = subprocess.Popen(
                ["bash", "-c", pts_cmd],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            for line in process.stdout:
                print(line, end="")
                log_f.write(line)
                stdout_f.write(line)
                log_f.flush()
                stdout_f.flush()
            process.wait()
            returncode = process.returncode

        print("[INFO] Recording CPU frequency after benchmark...")
        self.record_cpu_frequency(freq_end_file)

        pts_test_failed, pts_failure_reason = detect_pts_failure_from_log(log_file)
        if returncode == 0 and not pts_test_failed:
            print("\n[OK] Benchmark completed successfully")
            if self.perf_events and perf_stats_file.exists():
                try:
                    perf_summary = self.parse_perf_stats(perf_stats_file, cpu_list)
                    with open(perf_summary_file, "w") as handle:
                        json.dump(perf_summary, handle, indent=2)
                except Exception as e:
                    print(f"  [WARN] Failed to parse perf stats: {e}")
            return True

        reason = pts_failure_reason or f"returncode={returncode}"
        print(f"\n[ERROR] Benchmark failed: {reason}")
        return False

    def export_results(self):
        pts_results_dir = Path.home() / ".phoronix-test-suite" / "test-results"
        for num_threads in self.thread_list:
            result_name = f"{self.benchmark}-{num_threads}threads"
            result_dir_name = result_name.replace(".", "")
            result_dir = pts_results_dir / result_dir_name
            if not result_dir.exists():
                print(f"[WARN] Result not found: {result_dir}")
                continue

            csv_output = self.results_dir / f"{num_threads}-thread.csv"
            print(f"  [EXPORT] CSV: {csv_output}")
            result = subprocess.run(["phoronix-test-suite", "result-file-to-csv", result_dir_name], capture_output=True, text=True)
            if result.returncode == 0:
                home_csv = Path.home() / f"{result_dir_name}.csv"
                if home_csv.exists():
                    shutil.move(str(home_csv), str(csv_output))
                    print(f"  [OK] Saved: {csv_output}")
            else:
                print(f"  [WARN] CSV export failed: {result.stderr}")

            json_output = self.results_dir / f"{num_threads}-thread.json"
            print(f"  [EXPORT] JSON: {json_output}")
            result = subprocess.run(["phoronix-test-suite", "result-file-to-json", result_dir_name], capture_output=True, text=True)
            if result.returncode == 0:
                home_json = Path.home() / f"{result_dir_name}.json"
                if home_json.exists():
                    shutil.move(str(home_json), str(json_output))
                    print(f"  [OK] Saved: {json_output}")
            else:
                print(f"  [WARN] JSON export failed: {result.stderr}")

        print("\n[OK] Export completed")

    def generate_summary(self):
        print(f"\n{'=' * 80}")
        print(">>> Generating summary")
        print(f"{'=' * 80}")

        summary_log = self.results_dir / "summary.log"
        summary_json_file = self.results_dir / "summary.json"
        all_results = []

        for num_threads in self.thread_list:
            json_file = self.results_dir / f"{num_threads}-thread.json"
            if not json_file.exists():
                continue
            with open(json_file, "r") as handle:
                data = json.load(handle)
                for _result_id, result in data.get("results", {}).items():
                    for _system_id, system_result in result.get("results", {}).items():
                        all_results.append(
                            {
                                "threads": num_threads,
                                "value": system_result.get("value"),
                                "raw_values": system_result.get("raw_values", []),
                                "test_name": result.get("title"),
                                "description": result.get("description"),
                                "unit": result.get("scale"),
                            }
                        )

        if not all_results:
            print("[WARN] No results found for summary generation")
            return

        with open(summary_log, "w") as handle:
            handle.write("=" * 80 + "\n")
            handle.write("Benchmark Summary\n")
            handle.write(f"Machine: {self.machine_name}\n")
            handle.write(f"Test Category: {self.test_category}\n")
            handle.write("=" * 80 + "\n\n")
            for result in all_results:
                handle.write(f"Threads: {result['threads']}\n")
                handle.write(f"  Test: {result['test_name']}\n")
                handle.write(f"  Description: {result['description']}\n")
                val_str = f"{result['value']:.2f}" if result["value"] is not None else "FAILED"
                handle.write(f"  Average: {val_str} {result['unit']}\n\n")
        print(f"[OK] Summary log saved: {summary_log}")

        summary_data = {
            "benchmark": self.benchmark,
            "test_category": self.test_category,
            "machine": self.machine_name,
            "vcpu_count": self.vcpu_count,
            "results": all_results,
        }
        with open(summary_json_file, "w") as handle:
            json.dump(summary_data, handle, indent=2)
        print(f"[OK] Summary JSON saved: {summary_json_file}")

    def check_and_setup_perf_permissions(self):
        try:
            with open("/proc/sys/kernel/perf_event_paranoid", "r") as handle:
                current = int(handle.read().strip())
            if current >= 1:
                result = subprocess.run(
                    ["sudo", "sysctl", "-w", "kernel.perf_event_paranoid=0"],
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    print("  [OK] perf_event_paranoid adjusted to 0")
                    return 0
                print("  [WARN] Could not lower perf_event_paranoid (sudo required)")
                return current
            return current
        except Exception:
            return 2

    def get_perf_events(self):
        perf_path = shutil.which("perf")
        if not perf_path:
            return None
        hw_events = "cycles,instructions,cpu-clock,task-clock,context-switches,cpu-migrations"
        try:
            result = subprocess.run(
                ["bash", "-c", f"{perf_path} stat -e {hw_events} sleep 0.01 2>&1"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            output = result.stdout + result.stderr
            if result.returncode == 0 and "<not supported>" not in output:
                return hw_events

            sw_events = "cpu-clock,task-clock,context-switches,cpu-migrations"
            result_sw = subprocess.run(
                ["bash", "-c", f"{perf_path} stat -e {sw_events} sleep 0.01 2>&1"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if result_sw.returncode == 0:
                return sw_events
        except Exception:
            pass
        return None

    def ensure_upload_disabled(self):
        config_path = Path.home() / ".phoronix-test-suite" / "user-config.xml"
        if not config_path.exists():
            return
        try:
            content = config_path.read_text()
            if "<UploadResults>TRUE</UploadResults>" in content:
                config_path.write_text(content.replace("<UploadResults>TRUE</UploadResults>", "<UploadResults>FALSE</UploadResults>"))
                print("  [OK] UploadResults set to FALSE")
        except Exception as e:
            print(f"  [WARN] Failed to update user-config.xml: {e}")

    def run(self):
        print("=" * 80)
        print(f"PTS Benchmark Runner : {self.benchmark}")
        print(f"Machine              : {self.machine_name}")
        print(f"OS                   : {self.os_name}")
        print(f"vCPU count           : {self.vcpu_count}")
        print(f"Thread List          : {self.thread_list}")
        print(f"Quick mode           : {self.quick_mode}")
        print(f"Results dir          : {self.results_dir}")
        print("=" * 80)

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

        self.results_dir.mkdir(parents=True, exist_ok=True)
        for num_threads in self.thread_list:
            prefix = f"{num_threads}-thread"
            thread_dir = self.results_dir / prefix
            if thread_dir.exists():
                shutil.rmtree(thread_dir)
            for file_path in self.results_dir.glob(f"{prefix}.*"):
                file_path.unlink()
            print(f"  [INFO] Cleaned existing {prefix} results (other threads preserved)")

        failed = []
        for num_threads in self.thread_list:
            print(f"\n{'=' * 80}")
            print(f">>> Running {self.benchmark} with {num_threads} thread(s)")
            print(f"{'=' * 80}")
            if not self.run_benchmark(num_threads):
                print(f"[ERROR] Benchmark failed for {num_threads} thread(s)")
                failed.append(num_threads)

        print(f"\n{'=' * 80}")
        print(">>> Exporting results")
        print(f"{'=' * 80}")
        self.export_results()
        self.generate_summary()
        cleanup_pts_artifacts(self.benchmark)

        if failed:
            print(f"\n[WARN] Failed thread counts: {failed}")
        else:
            print(f"\n{'=' * 80}")
            print("[SUCCESS] All benchmarks completed successfully")
            print(f"{'=' * 80}")

        return len(failed) == 0


def main():
    parser = argparse.ArgumentParser(description="Run apache-siege-1.1.0 benchmark")
    parser.add_argument("threads_pos", nargs="?", type=int, help="Number of threads (optional, omit for scaling mode)")
    parser.add_argument("--threads", type=int, help="Run benchmark with specified number of threads only (1 to CPU count)")
    parser.add_argument("--quick", action="store_true", help="Quick mode: Run each test only once (for development/testing)")
    args = parser.parse_args()

    if args.quick:
        print("[INFO] Quick mode enabled: FORCE_TIMES_TO_RUN=1")
        print("[INFO] Tests will run once instead of 3 times")

    threads = args.threads if args.threads is not None else args.threads_pos
    runner = ApacheSiegeRunner(threads_arg=threads, quick_mode=args.quick)
    success = runner.run()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
