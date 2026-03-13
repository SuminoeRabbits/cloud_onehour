#!/usr/bin/env python3
"""
PTS Runner for srsran-2.5.0

Source: https://openbenchmarking.org/innhold/ca7d2455ab40a7020fe14402ea7d7fc186f28322
AppVersion: 25.10 (srsRAN_Project-release_25_10)

System Dependencies:
- build-utilities    : build-essential (Ubuntu) / gcc gcc-c++ make (RHEL)
- fftw3-development  : libfftw3-dev (Ubuntu) / fftw-devel (RHEL)
- cmake              : cmake
- boost-development  : libboost-all-dev (Ubuntu) / boost-devel (RHEL)
- libconfigpp        : libconfig++-dev (Ubuntu) / libconfig-devel (RHEL)
- libmbedtls         : libmbedtls-dev (Ubuntu) / mbedtls-devel (RHEL)
- libsctp            : libsctp-dev (Ubuntu) / lksctp-tools-devel (RHEL)
- yaml-cpp           : libyaml-cpp-dev (Ubuntu) / yaml-cpp-devel (RHEL)
- libgtest           : libgtest-dev (Ubuntu) / gtest-devel (RHEL)

- Environment Size : ~294 MB
- Test Type        : Processor (5G PHY layer benchmark)
- Supported Platforms: Linux, MacOSX

Test Options (4 combinations, all run via BATCH_MODE):
  1. PUSCH Processor Benchmark, Throughput Total
  2. PUSCH Processor Benchmark, Throughput Thread
  3. PDSCH Processor Benchmark, Throughput Total
  4. PDSCH Processor Benchmark, Throughput Thread

Result Scale: Mb/s  (Higher Is Better)
TimesToRun  : 3

Test Characteristics:
- Multi-threaded     : Yes
- THFix_in_compile   : false
- THChange_at_runtime: true
  Thread count is injected into test-definition.xml before each batch-run:
    * throughput_total options  → append -T {num_threads}
    * throughput_thread options → replace -T 1 (and -t 0) with -T {num_threads}
  test-definition.xml is backed up before patching and restored after each run.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from runner_common import detect_pts_failure_from_log, get_install_status, cleanup_pts_artifacts


# ---------------------------------------------------------------------------
# PreSeedDownloader
# ---------------------------------------------------------------------------

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

    def download_from_xml(self, benchmark_name, threshold_mb=256):
        """Parse downloads.xml and accelerate large files with aria2c."""
        if not self.aria2_available:
            return False

        profile_path = (
            Path.home()
            / ".phoronix-test-suite"
            / "test-profiles"
            / benchmark_name
            / "downloads.xml"
        )
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
                print(f"  [WARN] downloads.xml still missing: {profile_path}")
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
                    size_bytes = self._get_remote_file_size(url)
                if size_bytes > 0:
                    size_mb = size_bytes / (1024 * 1024)
                    if size_mb >= threshold_mb:
                        print(f"  [INFO] {filename} is large ({size_mb:.1f} MB), accelerating with aria2c...")
                        self._ensure_file(urls, filename)
        except Exception as e:
            print(f"  [ERROR] Failed to parse downloads.xml: {e}")
            return False
        return True

    def _get_remote_file_size(self, url):
        try:
            result = subprocess.run(
                ["curl", "-s", "-I", "-L", url],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                return -1
            for line in result.stdout.splitlines():
                if line.lower().startswith("content-length:"):
                    try:
                        return int(line.split(":")[1].strip())
                    except ValueError:
                        pass
        except Exception:
            pass
        return -1

    def _ensure_file(self, urls, filename, size_bytes=-1):
        target_path = self.cache_dir / filename
        if target_path.exists():
            if size_bytes > 0 and target_path.stat().st_size != size_bytes:
                print(f"  [CACHE] Size mismatch for {filename}, re-downloading...")
            else:
                print(f"  [CACHE] File found: {filename}")
                return True
        if isinstance(urls, str):
            urls = [urls]
        print(f"  [ARIA2] Downloading {filename}...")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        _LARGE_FILE_THRESHOLD_BYTES = 10 * 1024 * 1024 * 1024
        num_conn = "4" if size_bytes >= _LARGE_FILE_THRESHOLD_BYTES else "16"
        cmd = [
            "aria2c", f"-x{num_conn}", f"-s{num_conn}",
            "--connect-timeout=30", "--timeout=120",
            "--max-tries=2", "--retry-wait=5", "--continue=true",
            "-d", str(self.cache_dir), "-o", filename
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
        except subprocess.CalledProcessError as e:
            print(f"  [ERROR] aria2c download failed for {filename}: {e}")
            if target_path.exists():
                target_path.unlink()
            return False
class SrsranRunner:
    """PTS runner for pts/srsran-2.5.0 (5G PHY processor benchmark)."""

    def __init__(self, threads_arg=None, quick_mode=False):
        # Benchmark identification
        self.benchmark = "srsran-2.5.0"
        self.benchmark_full = f"pts/{self.benchmark}"
        self.test_category = "Telecom"
        self.test_category_dir = self.test_category.replace(' ', '_')

        # System info
        self.vcpu_count = os.cpu_count() or 1
        self.machine_name = os.environ.get("MACHINE_NAME", os.uname().nodename)
        self.os_name = self.get_os_name()

        # Thread list
        if threads_arg is None:
            n_4 = self.vcpu_count // 4
            self.thread_list = [n_4, n_4 * 2, n_4 * 3, self.vcpu_count]
            self.thread_list = sorted(list(set([t for t in self.thread_list if t > 0])))
        else:
            n = min(threads_arg, self.vcpu_count)
            self.thread_list = [n]

        # Directories
        self.script_dir = Path(__file__).parent.resolve()
        self.project_root = self.script_dir.parent
        self.results_dir = self.project_root / "results" / self.machine_name / self.os_name / self.test_category_dir / self.benchmark

        self.quick_mode = quick_mode

        # WSL detection (informational only)
        self.is_wsl_env = self.is_wsl()
        if self.is_wsl_env:
            print("  [INFO] Running on WSL environment")

        # Perf setup (must call check_and_setup before get_perf_events)
        self.perf_paranoid = self.check_and_setup_perf_permissions()
        self.perf_events = self.get_perf_events()

        self.ensure_upload_disabled()
        if self.perf_events:
            print(f"  [OK] Perf monitoring enabled with events: {self.perf_events}")
        else:
            print("  [INFO] Perf monitoring disabled (command missing or unsupported)")

    # ------------------------------------------------------------------
    # Main flow
    # ------------------------------------------------------------------

    def run(self):
        """Main execution method. Returns True on success."""
        print('=' * 80)
        print(f"PTS Benchmark Runner: {self.benchmark}")
        print(f"Machine: {self.machine_name}")
        print(f"OS: {self.os_name}")
        print(f"vCPU Count: {self.vcpu_count}")
        print(f"Thread List: {self.thread_list}")
        print(f"Quick Mode: {self.quick_mode}")
        print(f"Results Directory: {self.results_dir}")
        print('=' * 80)

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
            self.install_benchmark()
        else:
            print(f"[INFO] Benchmark already installed, skipping installation: {self.benchmark_full}")

        failed = []
        for num_threads in self.thread_list:
            print('\n' + '=' * 80)
            print(f">>> Running {self.benchmark} with {num_threads} thread(s)")
            print('=' * 80)
            if not self.run_benchmark(num_threads):
                print(f"[ERROR] Benchmark failed for {num_threads} thread(s)")
                failed.append(num_threads)

        print('\n' + '=' * 80)
        print(">>> Exporting results")
        print('=' * 80)
        self.export_results()

        self.generate_summary()

        cleanup_pts_artifacts(self.benchmark)

        if failed:
            print(f"\n[WARN] Failed thread counts: {failed}")
        else:
            print('\n' + '=' * 80)
            print("[SUCCESS] All benchmarks completed successfully")
            print('=' * 80)

        # CRITICAL: Must return True for cloud_exec.py integration
        return len(failed) == 0

    # ------------------------------------------------------------------
    # System dependency installation
    # ------------------------------------------------------------------

    def install_system_deps(self):
        """Install OS-specific build and library dependencies for srsRAN."""
        print("\n>>> Installing system dependencies for srsRAN...")

        # Detect package manager
        has_apt = shutil.which("apt-get") is not None
        has_dnf = shutil.which("dnf") is not None
        has_yum = shutil.which("yum") is not None

        if has_apt:
            pkgs = [
                "build-essential", "cmake",
                "libfftw3-dev", "libboost-all-dev",
                "libconfig++-dev", "libmbedtls-dev",
                "libsctp-dev", "libyaml-cpp-dev",
                "libgtest-dev",
            ]
            cmd = ["sudo", "apt-get", "install", "-y"] + pkgs
        elif has_dnf:
            pkgs = [
                "gcc", "gcc-c++", "make", "cmake",
                "fftw-devel", "boost-devel",
                "libconfig-devel", "mbedtls-devel",
                "lksctp-tools-devel", "yaml-cpp-devel",
                "gtest-devel",
            ]
            cmd = ["sudo", "dnf", "install", "-y"] + pkgs
        elif has_yum:
            pkgs = [
                "gcc", "gcc-c++", "make", "cmake",
                "fftw-devel", "boost-devel",
                "libconfig-devel",
                "lksctp-tools-devel",
                "gtest-devel",
            ]
            cmd = ["sudo", "yum", "install", "-y"] + pkgs
        else:
            print("  [WARN] No supported package manager found; skipping system dep install")
            return

        print(f"  [INFO] Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=False)
        if result.returncode != 0:
            print(f"  [WARN] System dependency install returned {result.returncode}; continuing anyway")
        else:
            print("  [OK] System dependencies installed")

    # ------------------------------------------------------------------
    # Benchmark installation
    # ------------------------------------------------------------------

    def install_benchmark(self):
        """Install pts/srsran-2.5.0 via phoronix-test-suite batch-install."""
        print('\n' + '=' * 80)
        print(f">>> Installing {self.benchmark_full}")
        print('=' * 80)

        # Install system libraries first
        self.install_system_deps()

        # Pre-seed large downloads via aria2c
        downloader = PreSeedDownloader()
        if downloader.is_aria2_available():
            print("  [INFO] Pre-seeding downloads with aria2c...")
            downloader.download_from_xml(self.benchmark_full)
        else:
            print("  [INFO] aria2c not found; PTS will handle download")

        # Remove previous installation
        remove_cmd = f'echo "y" | phoronix-test-suite remove-installed-test "{self.benchmark_full}"'
        subprocess.run(
            ["bash", "-c", remove_cmd],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

        # Install log setup
        install_log_env = os.environ.get("PTS_INSTALL_LOG", "").strip().lower()
        install_log_path_env = os.environ.get("PTS_INSTALL_LOG_PATH", "").strip()
        use_install_log = install_log_env in {"1", "true", "yes"} or bool(install_log_path_env)
        install_log = (
            Path(install_log_path_env)
            if install_log_path_env
            else (self.results_dir / "install.log")
        )
        if use_install_log:
            self.results_dir.mkdir(parents=True, exist_ok=True)
            print(f"  [INFO] Install log: {install_log}")

        nproc = os.cpu_count() or 1
        install_cmd = (
            f'MAKEFLAGS="-j{nproc}" '
            'BATCH_MODE=1 SKIP_ALL_PROMPTS=1 '
            f'phoronix-test-suite batch-install {self.benchmark_full}'
        )

        print(f"  [INFO] Running: {install_cmd}")
        self.results_dir.mkdir(parents=True, exist_ok=True)
        log_file = install_log

        install_output = []
        process = subprocess.Popen(
            ["bash", "-c", install_cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        for line in process.stdout:
            print(line, end="")
            install_output.append(line)
            if use_install_log:
                pass  # written below after wait
        process.wait()
        returncode = process.returncode

        # Write install log
        try:
            with open(log_file, "w") as f:
                f.writelines(install_output)
        except Exception as e:
            print(f"  [WARN] Could not write install log: {e}")

        # Failure detection
        full_output = "".join(install_output)
        pts_test_failed, pts_failure_reason = detect_pts_failure_from_log(log_file)

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
            print(f"\n  [ERROR] Installation failed (returncode={returncode})")
            if pts_failure_reason:
                print(f"  [ERROR] Reason: {pts_failure_reason}")
            for line in install_output[-20:]:
                print(f"    {line}", end="")
            sys.exit(1)

        # Verify installation
        install_dir = (
            Path.home()
            / ".phoronix-test-suite"
            / "installed-tests"
            / "pts"
            / self.benchmark
        )
        if not install_dir.exists():
            print(f"  [ERROR] Installation directory not found: {install_dir}")
            sys.exit(1)

        verify_result = subprocess.run(
            ["phoronix-test-suite", "test-installed", self.benchmark_full],
            capture_output=True, text=True,
        )
        if self.benchmark_full not in verify_result.stdout:
            print(f"  [WARN] {self.benchmark_full} may not be fully recognized by PTS")

        print("  [OK] Installation completed and verified")

    # ------------------------------------------------------------------
    # test-definition.xml patching
    # ------------------------------------------------------------------

    def _test_definition_path(self):
        return (
            Path.home()
            / ".phoronix-test-suite"
            / "installed-tests"
            / "pts"
            / self.benchmark
            / "test-definition.xml"
        )

    def patch_test_definition(self, num_threads):
        """Inject -T num_threads into all test option Values.

        - throughput_total  options: append  -T {num_threads}
        - throughput_thread options: replace  -T 1 [-t 0] with -T {num_threads}

        Backup saved as test-definition.xml.bak before first patch.
        Returns True if patched successfully.
        """
        xml_path = self._test_definition_path()
        bak_path = xml_path.with_suffix(".xml.bak")

        if not xml_path.exists():
            print(f"  [WARN] test-definition.xml not found: {xml_path}")
            return False

        # Backup (once per install)
        if not bak_path.exists():
            shutil.copy2(xml_path, bak_path)
            print(f"  [INFO] Backed up test-definition.xml -> {bak_path.name}")

        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()

            patched = 0
            for entry in root.iter("Entry"):
                value_el = entry.find("Value")
                if value_el is None or value_el.text is None:
                    continue
                val = value_el.text.strip()

                if "-m throughput_thread" in val:
                    # Replace existing -T N [-t 0] with -T {num_threads}
                    # PUSCH thread: -T 1 -t 0  PDSCH thread: -T 1
                    val = re.sub(r"-T\s+\d+(\s+-t\s+\d+)?", f"-T {num_threads}", val)
                elif "-m throughput_total" in val:
                    # Append -T {num_threads} before -P flag (or at end)
                    if "-T " in val:
                        val = re.sub(r"-T\s+\d+", f"-T {num_threads}", val)
                    else:
                        val = re.sub(r"(-P\s+\S+)", rf"-T {num_threads} \1", val)

                value_el.text = val
                patched += 1

            if patched == 0:
                print("  [WARN] No Entry values found to patch in test-definition.xml")

            ET.indent(tree, space="  ")
            tree.write(xml_path, encoding="unicode", xml_declaration=False)
            print(f"  [INFO] Patched test-definition.xml: -T {num_threads} ({patched} entries)")
            return True

        except Exception as e:
            print(f"  [ERROR] Failed to patch test-definition.xml: {e}")
            # Restore from backup on error
            if bak_path.exists():
                shutil.copy2(bak_path, xml_path)
                print("  [INFO] Restored test-definition.xml from backup")
            return False

    def restore_test_definition(self):
        """Restore test-definition.xml from backup."""
        xml_path = self._test_definition_path()
        bak_path = xml_path.with_suffix(".xml.bak")
        if bak_path.exists():
            shutil.copy2(bak_path, xml_path)
            print("  [INFO] Restored test-definition.xml from backup")
        else:
            print("  [WARN] Backup not found; test-definition.xml may remain patched")

    # ------------------------------------------------------------------
    # Benchmark execution
    # ------------------------------------------------------------------

    def run_benchmark(self, num_threads):
        """Run benchmark for a given thread count with optional perf monitoring."""
        self.results_dir.mkdir(parents=True, exist_ok=True)
        log_file = self.results_dir / f"{num_threads}-thread.log"
        stdout_log = self.results_dir / "stdout.log"

        perf_stats_file = self.results_dir / f"{num_threads}-thread_perf_stats.txt"
        freq_start_file = self.results_dir / f"{num_threads}-thread_freq_start.txt"
        freq_end_file = self.results_dir / f"{num_threads}-thread_freq_end.txt"
        perf_summary_file = self.results_dir / f"{num_threads}-thread_perf_summary.json"

        # Patch test-definition.xml to inject thread count
        patched = self.patch_test_definition(num_threads)

        try:
            # Remove previous PTS result to avoid interactive prompts
            sanitized = self.benchmark.replace(".", "")
            for name in [
                f"{self.benchmark}-{num_threads}threads",
                f"{sanitized}-{num_threads}threads",
            ]:
                subprocess.run(
                    ["phoronix-test-suite", "remove-result", name],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )

            # Build PTS command
            if num_threads >= self.vcpu_count:
                cpu_list = ",".join(str(i) for i in range(self.vcpu_count))
                pts_base_cmd = f"phoronix-test-suite batch-run {self.benchmark_full}"
            else:
                cpu_list = self.get_cpu_affinity_list(num_threads)
                pts_base_cmd = f"taskset -c {cpu_list} phoronix-test-suite batch-run {self.benchmark_full}"

            quick_env = "FORCE_TIMES_TO_RUN=1 " if self.quick_mode else ""
            batch_env = (
                f"{quick_env}"
                "BATCH_MODE=1 SKIP_ALL_PROMPTS=1 DISPLAY_COMPACT_RESULTS=1 "
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
                pts_cmd = f"NUM_CPU_CORES={num_threads} {batch_env} {perf_cmd} {pts_base_cmd}"
            else:
                pts_cmd = f"NUM_CPU_CORES={num_threads} {batch_env} {pts_base_cmd}"
                print("  [INFO] Running without perf")

            # Record CPU frequency before
            print("[INFO] Recording CPU frequency before benchmark...")
            if self.record_cpu_frequency(freq_start_file):
                print("  [OK] Start frequency recorded")
            else:
                print("  [WARN] CPU frequency not available (common on ARM64/cloud VMs)")

            # Execute benchmark with real-time streaming
            with open(log_file, "w") as log_f, open(stdout_log, "a") as stdout_f:
                stdout_f.write('\n' + '=' * 80 + '\n')
                stdout_f.write(f"[PTS BENCHMARK COMMAND - {num_threads} thread(s)]\n")
                stdout_f.write(f"{pts_cmd}\n")
                stdout_f.write('=' * 80 + '\n\n')
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

            # Record CPU frequency after
            if self.record_cpu_frequency(freq_end_file):
                print("  [OK] End frequency recorded")
            else:
                print("  [WARN] CPU frequency not available")

            # Failure detection (returncode + log content)
            pts_test_failed, failure_reason = detect_pts_failure_from_log(log_file)
            if returncode == 0 and not pts_test_failed:
                print(f"\n[OK] Benchmark completed successfully for {num_threads} thread(s)")
                if self.perf_events and perf_stats_file.exists():
                    try:
                        perf_summary = self.parse_perf_stats_and_freq(
                            perf_stats_file, freq_start_file, freq_end_file, cpu_list
                        )
                        with open(perf_summary_file, "w") as f:
                            json.dump(perf_summary, f, indent=2)
                    except Exception as e:
                        print(f"  [WARN] Failed to parse perf stats: {e}")
                return True
            else:
                reason_str = failure_reason or f"returncode={returncode}"
                print(f"\n[ERROR] Benchmark failed for {num_threads} thread(s): {reason_str}")
                return False

        finally:
            if patched:
                self.restore_test_definition()

    # ------------------------------------------------------------------
    # Export and summary
    # ------------------------------------------------------------------

    def export_results(self):
        """Export PTS results to CSV and JSON for each thread count."""
        pts_results_dir = Path.home() / ".phoronix-test-suite" / "test-results"

        for num_threads in self.thread_list:
            result_name = f"{self.benchmark}-{num_threads}threads"
            # PTS removes dots from directory names
            result_dir_name = result_name.replace(".", "")
            result_dir = pts_results_dir / result_dir_name

            if not result_dir.exists():
                print(f"[WARN] Result not found: {result_dir}")
                print(f"       Expected: {result_name}, actual: {result_dir_name}")
                continue

            print(f"[DEBUG] result_name: {result_name}, result_dir_name: {result_dir_name}")

            # CSV
            csv_output = self.results_dir / f"{num_threads}-thread.csv"
            print(f"  [EXPORT] CSV: {csv_output}")
            result = subprocess.run(
                ["phoronix-test-suite", "result-file-to-csv", result_dir_name],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                home_csv = Path.home() / f"{result_dir_name}.csv"
                if home_csv.exists():
                    shutil.move(str(home_csv), str(csv_output))
                    print(f"  [OK] Saved: {csv_output}")
            else:
                print(f"  [WARN] CSV export failed: {result.stderr}")

            # JSON
            json_output = self.results_dir / f"{num_threads}-thread.json"
            print(f"  [EXPORT] JSON: {json_output}")
            result = subprocess.run(
                ["phoronix-test-suite", "result-file-to-json", result_dir_name],
                capture_output=True, text=True,
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
        print('\n' + '=' * 80)
        print(">>> Generating summary")
        print('=' * 80)

        summary_log = self.results_dir / "summary.log"
        summary_json_file = self.results_dir / "summary.json"

        all_results = []
        for num_threads in self.thread_list:
            json_file = self.results_dir / f"{num_threads}-thread.json"
            if not json_file.exists():
                continue
            try:
                with open(json_file, "r") as f:
                    data = json.load(f)
                for result_id, result in data.get("results", {}).items():
                    for system_id, system_result in result.get("results", {}).items():
                        all_results.append({
                            "threads": num_threads,
                            "value": system_result.get("value"),
                            "raw_values": system_result.get("raw_values", []),
                            "test_name": result.get("title"),
                            "description": result.get("description"),
                            "unit": result.get("scale"),
                        })
            except Exception as e:
                print(f"  [WARN] Could not read {json_file}: {e}")

        if not all_results:
            print("[WARN] No results found for summary generation")
            return

        # summary.log (human-readable)
        with open(summary_log, "w") as f:
            f.write("=" * 80 + "\n")
            f.write("Benchmark Summary\n")
            f.write(f"Benchmark  : {self.benchmark}\n")
            f.write(f"Machine    : {self.machine_name}\n")
            f.write(f"OS         : {self.os_name}\n")
            f.write(f"vCPU Count : {self.vcpu_count}\n")
            f.write("=" * 80 + "\n\n")
            f.write(f"{'Threads':<10} {'Value':<15} {'Unit':<20} Test\n")
            f.write("-" * 80 + "\n")
            for r in all_results:
                # None guard: must not apply :.2f to None
                val_str = f"{r['value']:<15.2f}" if r["value"] is not None else "FAILED         "
                f.write(f"{r['threads']:<10} {val_str} {str(r['unit']):<20} {r['test_name']}\n")

        print(f"[OK] Summary log saved: {summary_log}")

        # summary.json (AI-friendly)
        summary_data = {
            "benchmark": self.benchmark,
            "test_category": self.test_category,
            "machine": self.machine_name,
            "vcpu_count": self.vcpu_count,
            "results": all_results,
        }
        with open(summary_json_file, "w") as f:
            json.dump(summary_data, f, indent=2)
        print(f"[OK] Summary JSON saved: {summary_json_file}")

    # ------------------------------------------------------------------
    # OS / environment utilities
    # ------------------------------------------------------------------

    def get_os_name(self):
        """Return OS name formatted as <Distro>_<Version> (e.g. Ubuntu_22_04)."""
        try:
            result = subprocess.run(
                ["lsb_release", "-d", "-s"],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                parts = result.stdout.strip().split()
                if len(parts) >= 2:
                    return f"{parts[0]}_{parts[1].replace('.', '_')}"
        except Exception:
            pass
        try:
            with open("/etc/os-release", "r") as f:
                info = {}
                for line in f:
                    if "=" in line:
                        k, v = line.strip().split("=", 1)
                        info[k] = v.strip('"')
            if "NAME" in info and "VERSION_ID" in info:
                distro = info["NAME"].split()[0]
                version = info["VERSION_ID"].replace(".", "_")
                return f"{distro}_{version}"
        except Exception:
            pass
        return "Unknown_OS"

    def is_wsl(self):
        """Return True if running inside WSL."""
        try:
            if not os.path.exists("/proc/version"):
                return False
            with open("/proc/version", "r") as f:
                content = f.read().lower()
            return "microsoft" in content or "wsl" in content
        except Exception:
            return False

    def get_cpu_affinity_list(self, n):
        """Return comma-separated CPU list optimised for HyperThreading."""
        half = self.vcpu_count // 2
        if n <= half:
            cpu_list = [str(i * 2) for i in range(n)]
        else:
            cpu_list = [str(i * 2) for i in range(half)]
            logical = n - half
            cpu_list.extend(str(i * 2 + 1) for i in range(logical))
        return ",".join(cpu_list)

    def get_cpu_frequencies(self):
        """Return list of CPU frequencies in kHz (cross-platform)."""
        frequencies = []

        # Method 1: /proc/cpuinfo (x86_64)
        try:
            result = subprocess.run(
                ["bash", "-c", 'grep "cpu MHz" /proc/cpuinfo'],
                capture_output=True, text=True,
            )
            if result.returncode == 0 and result.stdout.strip():
                for line in result.stdout.strip().split("\n"):
                    parts = line.split(":")
                    if len(parts) >= 2:
                        frequencies.append(int(float(parts[1].strip()) * 1000))
                if frequencies:
                    return frequencies
        except Exception:
            pass

        # Method 2: /sys/devices/system/cpu/cpufreq (ARM64)
        try:
            freq_files = sorted(
                Path("/sys/devices/system/cpu").glob("cpu[0-9]*/cpufreq/scaling_cur_freq")
            )
            if not freq_files:
                freq_files = sorted(
                    Path("/sys/devices/system/cpu").glob("cpu[0-9]*/cpufreq/cpuinfo_cur_freq")
                )
            for freq_file in freq_files:
                try:
                    frequencies.append(int(freq_file.read_text().strip()))
                except Exception:
                    frequencies.append(0)
            if frequencies:
                return frequencies
        except Exception:
            pass

        # Method 3: lscpu (fallback)
        try:
            result = subprocess.run(["lscpu"], capture_output=True, text=True)
            if result.returncode == 0:
                for line in result.stdout.split("\n"):
                    if "CPU MHz" in line or "CPU max MHz" in line:
                        parts = line.split(":")
                        if len(parts) >= 2:
                            mhz = float(parts[1].strip().replace(",", "."))
                            return [int(mhz * 1000)] * self.vcpu_count
        except Exception:
            pass

        return frequencies

    def record_cpu_frequency(self, output_file):
        """Write CPU frequencies to file; return True on success."""
        frequencies = self.get_cpu_frequencies()
        if frequencies:
            try:
                with open(output_file, "w") as f:
                    for freq in frequencies:
                        f.write(f"{freq}\n")
                return True
            except Exception as e:
                print(f"  [WARN] Failed to write frequency file: {e}")
                return False
        else:
            try:
                open(output_file, "w").close()
            except Exception:
                pass
            return False

    def parse_perf_stats_and_freq(self, perf_stats_file, freq_start_file, freq_end_file, cpu_list):
        """Parse perf stat output and return metrics dict."""
        if not self.perf_events or not perf_stats_file.exists():
            return {"note": "perf monitoring not available", "cpu_list": cpu_list}

        cpu_ids = [int(c.strip()) for c in cpu_list.split(",")]
        per_cpu_metrics = {cpu_id: {} for cpu_id in cpu_ids}

        try:
            with open(perf_stats_file, "r") as f:
                for line in f:
                    m = re.match(r"CPU(\d+)\s+([\d,.<>a-zA-Z\s]+)\s+([a-zA-Z0-9\-_]+)", line)
                    if m:
                        cpu_num = int(m.group(1))
                        value_str = m.group(2).strip()
                        event = m.group(3)
                        if cpu_num in per_cpu_metrics and "<not supported>" not in value_str:
                            try:
                                value = float(value_str.split()[0].replace(",", ""))
                                per_cpu_metrics[cpu_num][event] = value
                            except ValueError:
                                continue
        except FileNotFoundError:
            print(f"  [INFO] perf stats file not found (perf may be unsupported on this VM): {perf_stats_file}")
        except Exception as e:
            print(f"  [WARN] Failed to parse perf stat file: {e}")

        return {"per_cpu_metrics": per_cpu_metrics, "cpu_list": cpu_list}

    # ------------------------------------------------------------------
    # Perf utilities
    # ------------------------------------------------------------------

    def check_and_setup_perf_permissions(self):
        """Check and optionally lower perf_event_paranoid. Returns current value."""
        print('\n' + '=' * 80)
        print(">>> Checking perf_event_paranoid setting")
        print('=' * 80)
        try:
            result = subprocess.run(
                ["cat", "/proc/sys/kernel/perf_event_paranoid"],
                capture_output=True, text=True, check=True,
            )
            current_value = int(result.stdout.strip())
            print(f"  [INFO] Current perf_event_paranoid: {current_value}")
            if current_value >= 1:
                print(f"  [WARN] perf_event_paranoid={current_value}; attempting to set to 0...")
                r = subprocess.run(
                    ["sudo", "sysctl", "-w", "kernel.perf_event_paranoid=0"],
                    capture_output=True, text=True,
                )
                if r.returncode == 0:
                    print("  [OK] perf_event_paranoid set to 0 (until reboot)")
                    return 0
                else:
                    print("  [WARN] Could not lower perf_event_paranoid (sudo required)")
                    return current_value
            else:
                print(f"  [OK] perf_event_paranoid={current_value} is acceptable")
                return current_value
        except Exception as e:
            print(f"  [WARN] Could not check perf_event_paranoid: {e}")
            return 2

    def get_perf_events(self):
        """Detect available perf events (hw+sw → sw-only → None)."""
        perf_path = shutil.which("perf")
        if not perf_path:
            print("  [INFO] perf command not found in PATH")
            return None

        hw_events = "cycles,instructions,cpu-clock,task-clock,context-switches,cpu-migrations"
        try:
            result = subprocess.run(
                ["bash", "-c", f"{perf_path} stat -e {hw_events} sleep 0.01 2>&1"],
                capture_output=True, text=True, timeout=3,
            )
            output = result.stdout + result.stderr
            if result.returncode == 0 and "<not supported>" not in output:
                print("  [OK] Hardware PMU available")
                return hw_events

            sw_events = "cpu-clock,task-clock,context-switches,cpu-migrations"
            result_sw = subprocess.run(
                ["bash", "-c", f"{perf_path} stat -e {sw_events} sleep 0.01 2>&1"],
                capture_output=True, text=True, timeout=3,
            )
            if result_sw.returncode == 0:
                print("  [INFO] Using software-only perf events")
                return sw_events
        except subprocess.TimeoutExpired:
            print("  [WARN] perf test timed out")
        except Exception as e:
            print(f"  [DEBUG] perf test failed: {e}")

        print("  [INFO] perf not functional (permission or kernel issue)")
        return None

    def ensure_upload_disabled(self):
        """Ensure PTS result upload is disabled."""
        config_path = Path.home() / ".phoronix-test-suite" / "user-config.xml"
        if not config_path.exists():
            return
        try:
            content = config_path.read_text()
            if "<UploadResults>TRUE</UploadResults>" in content:
                print("  [WARN] UploadResults=TRUE found; disabling...")
                content = content.replace(
                    "<UploadResults>TRUE</UploadResults>",
                    "<UploadResults>FALSE</UploadResults>",
                )
                config_path.write_text(content)
                print("  [OK] UploadResults set to FALSE")
        except Exception as e:
            print(f"  [WARN] Failed to check user-config.xml: {e}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="PTS runner for srsran-2.5.0",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s           # Run with 4-point auto-scaling (nproc/4, /2, *3/4, nproc)
  %(prog)s 288       # Run with 288 threads only
  %(prog)s --quick   # Run in quick mode (FORCE_TIMES_TO_RUN=1)
        """,
    )
    parser.add_argument(
        "threads_pos", nargs="?", type=int, default=None,
        help="Number of threads (positional, optional; omit for auto-scaling)",
    )
    parser.add_argument(
        "--threads", type=int, default=None,
        help="Number of threads (named alternative to positional)",
    )
    parser.add_argument(
        "--quick", action="store_true",
        help="Quick mode: FORCE_TIMES_TO_RUN=1 (for development/testing)",
    )
    args = parser.parse_args()

    threads = args.threads if args.threads is not None else args.threads_pos

    if args.quick:
        print("[INFO] Quick mode enabled: FORCE_TIMES_TO_RUN=1")
        print("[INFO] Tests will run once instead of 3+ times (60-70% time reduction)")

    if threads is not None and threads < 1:
        print(f"[ERROR] Thread count must be >= 1 (got: {threads})")
        sys.exit(1)

    runner = SrsranRunner(threads_arg=threads, quick_mode=args.quick)
    success = runner.run()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
