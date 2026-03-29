#!/usr/bin/env python3
"""
PTS Runner for dacapobench-1.1.0

Source: https://openbenchmarking.org/innhold/942725ddf5e5a1e82fff48909bf1a4a15ada3d32

System Dependencies (from openbenchmarking.org / phoronix-test-suite info):
- Software Dependencies:
  * Java
- Estimated Install Time: 2 Seconds
- Environment Size: 15900 MB
- Test Type: Processor
- Supported Platforms: Linux, Solaris, BSD, MacOSX, Windows

Test Characteristics:
- Multi-threaded: Yes (varies by DaCapo workload; JVM manages parallelism internally)
- Honors CFLAGS/CXXFLAGS: N/A (Java-based)
- Notable Instructions: SVE2 support via JVM (OpenJDK 9+)
- THFix_in_compile: false - Thread count NOT fixed at compile time
- THChange_at_runtime: true - Runtime scaling is applied via CPU affinity / scheduler limits

Platform Notes:
- The upstream test profile declares only a generic `java` dependency and no OS-specific
  install logic. No OS-dependent handling is embedded in this runner.
- For RHEL9-family systems, provision Java with scripts_rhel9/setup_jdkxx.sh instead of
  adding distro-specific logic here.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from runner_common import cleanup_pts_artifacts, detect_pts_failure_from_log, get_install_status


class PreSeedDownloader:
    """
    Utility to pre-download large test files into the Phoronix Test Suite cache.
    """

    def __init__(self, cache_dir=None):
        if cache_dir:
            self.cache_dir = Path(cache_dir)
        else:
            self.cache_dir = Path.home() / ".phoronix-test-suite" / "download-cache"
        self.aria2_available = shutil.which("aria2c") is not None

    def is_aria2_available(self):
        return self.aria2_available

    def download_from_xml(self, benchmark_name, threshold_mb=512):
        if not self.aria2_available:
            print("  [INFO] aria2c not found, skipping pre-seed")
            return False

        profile_path = Path.home() / ".phoronix-test-suite" / "test-profiles" / benchmark_name / "downloads.xml"
        if not profile_path.exists():
            print(f"  [WARN] downloads.xml not found at {profile_path}")
            print(f"  [INFO] Attempting to fetch test profile via phoronix-test-suite info {benchmark_name}...")
            try:
                subprocess.run(
                    ["phoronix-test-suite", "info", benchmark_name],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception as exc:
                print(f"  [WARN] Failed to run phoronix-test-suite info: {exc}")
                return False
            if not profile_path.exists():
                print(f"  [WARN] downloads.xml still missing after info: {profile_path}")
                return False

        try:
            import xml.etree.ElementTree as ET

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

                urls = [u.strip() for u in (url_node.text or "").split(",") if u.strip()]
                filename = (filename_node.text or "").strip()
                if not urls or not filename:
                    continue

                size_bytes = -1
                if filesize_node is not None and filesize_node.text:
                    try:
                        size_bytes = int(filesize_node.text.strip())
                    except ValueError:
                        size_bytes = -1

                if size_bytes > 0 and size_bytes / (1024 * 1024) >= threshold_mb:
                    print(
                        f"  [INFO] {filename} is large ({size_bytes / (1024 * 1024):.1f} MB), "
                        "accelerating with aria2c..."
                    )
                    self.ensure_file(urls, filename, size_bytes=size_bytes)
        except Exception as exc:
            print(f"  [ERROR] Failed to parse downloads.xml: {exc}")
            return False

        return True

    def ensure_file(self, urls, filename, size_bytes=-1):
        large_file_threshold_bytes = 10 * 1024 * 1024 * 1024
        target_path = self.cache_dir / filename

        if target_path.exists():
            if size_bytes > 0:
                actual = target_path.stat().st_size
                if actual == size_bytes:
                    print(f"  [CACHE] Verified: {filename}")
                    return True
                print(f"  [WARN] Incomplete cache: {filename} ({actual}/{size_bytes} bytes). Resuming...")
            else:
                print(f"  [CACHE] File found: {filename}")
                return True

        num_conn = 4 if size_bytes > 0 and size_bytes >= large_file_threshold_bytes else 16
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            "aria2c",
            "-x",
            str(num_conn),
            "-s",
            str(num_conn),
            "--continue=true",
            "--connect-timeout=30",
            "--timeout=120",
            "--max-tries=2",
            "--retry-wait=5",
            "-d",
            str(self.cache_dir),
            "-o",
            filename,
        ] + urls
        try:
            subprocess.run(cmd, check=True, timeout=5400)
            print(f"  [OK] Pre-seeded: {filename}")
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            print(f"  [WARN] aria2c download failed: {exc}")
            if target_path.exists():
                target_path.unlink()
            return False


class DacapoBenchRunner:
    EXCLUDED_SUBTESTS = {
        "cassandra": "Apache Cassandra",
    }

    def __init__(self, threads_arg=None, quick_mode=False):
        self.benchmark = "dacapobench-1.1.0"
        self.benchmark_full = f"pts/{self.benchmark}"
        self.test_category = "Java Applications"
        self.test_category_dir = self.test_category.replace(" ", "_")

        self.vcpu_count = os.cpu_count() or 1
        self.machine_name = os.environ.get("MACHINE_NAME", os.uname().nodename)
        self.os_name = self.get_os_name()

        if threads_arg is None:
            n_4 = self.vcpu_count // 4
            self.thread_list = [n_4, n_4 * 2, n_4 * 3, self.vcpu_count]
            self.thread_list = sorted(list(set([t for t in self.thread_list if t > 0])))
        else:
            self.thread_list = [min(threads_arg, self.vcpu_count)]

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

        self.preseed = PreSeedDownloader()
        self.ensure_java_available()

    def get_os_name(self):
        try:
            result = subprocess.run(
                ["lsb_release", "-d", "-s"],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                parts = result.stdout.strip().split()
                if len(parts) >= 2:
                    return f"{parts[0]}_{parts[1].replace('.', '_')}"
        except Exception:
            pass

        try:
            info = {}
            with open("/etc/os-release", "r") as f:
                for line in f:
                    if "=" in line:
                        key, value = line.strip().split("=", 1)
                        info[key] = value.strip('"')
            if "NAME" in info and "VERSION_ID" in info:
                distro = info["NAME"].split()[0]
                version = info["VERSION_ID"].replace(".", "_")
                return f"{distro}_{version}"
        except Exception:
            pass

        return "Unknown_OS"

    def is_wsl(self):
        try:
            if not os.path.exists("/proc/version"):
                return False
            with open("/proc/version", "r") as f:
                content = f.read().lower()
            return "microsoft" in content or "wsl" in content
        except Exception:
            return False

    def get_cpu_affinity_list(self, n):
        half = max(1, (self.vcpu_count // 2))
        cpu_list = []
        if n <= half:
            cpu_list = [str(i * 2) for i in range(n) if i * 2 < self.vcpu_count]
        else:
            cpu_list = [str(i * 2) for i in range(half) if i * 2 < self.vcpu_count]
            logical_count = n - len(cpu_list)
            cpu_list.extend(
                [str(i * 2 + 1) for i in range(logical_count) if (i * 2 + 1) < self.vcpu_count]
            )
        cpu_list = cpu_list[:n]
        if not cpu_list:
            cpu_list = [str(i) for i in range(min(n, self.vcpu_count))]
        return ",".join(cpu_list)

    def get_cpu_frequencies(self):
        frequencies = []

        try:
            result = subprocess.run(
                ["bash", "-c", 'grep "cpu MHz" /proc/cpuinfo'],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                for line in result.stdout.strip().splitlines():
                    parts = line.split(":")
                    if len(parts) >= 2:
                        mhz = float(parts[1].strip())
                        frequencies.append(int(mhz * 1000))
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
            result = subprocess.run(["lscpu"], capture_output=True, text=True, check=False)
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if "CPU MHz" in line or "CPU max MHz" in line:
                        parts = line.split(":")
                        if len(parts) >= 2:
                            mhz = float(parts[1].strip().replace(",", "."))
                            return [int(mhz * 1000)] * self.vcpu_count
        except Exception:
            pass

        return frequencies

    def record_cpu_frequency(self, output_file):
        frequencies = self.get_cpu_frequencies()
        try:
            with open(output_file, "w") as f:
                for freq in frequencies:
                    f.write(f"{freq}\n")
            return bool(frequencies)
        except Exception as exc:
            print(f"  [WARN] Failed to write frequency file: {exc}")
            return False

    def get_perf_events(self):
        if not shutil.which("perf"):
            return None

        hw_events = "cycles,instructions,branches,branch-misses,cache-references,cache-misses"
        hw_test = subprocess.run(
            ["bash", "-c", f"perf stat -e {hw_events} -- sleep 0.01"],
            capture_output=True,
            text=True,
            check=False,
        )
        if hw_test.returncode == 0 and "not supported" not in (hw_test.stdout + hw_test.stderr):
            return hw_events

        sw_events = "cpu-clock,task-clock,context-switches,cpu-migrations,page-faults"
        sw_test = subprocess.run(
            ["bash", "-c", f"perf stat -e {sw_events} -- sleep 0.01"],
            capture_output=True,
            text=True,
            check=False,
        )
        if sw_test.returncode == 0 and "not supported" not in (sw_test.stdout + sw_test.stderr):
            return sw_events

        return None

    def check_and_setup_perf_permissions(self):
        try:
            result = subprocess.run(
                ["cat", "/proc/sys/kernel/perf_event_paranoid"],
                capture_output=True,
                text=True,
                check=True,
            )
            current_value = int(result.stdout.strip())
            if current_value >= 1:
                print("  [INFO] Attempting to adjust perf_event_paranoid to 0...")
                result = subprocess.run(
                    ["sudo", "sysctl", "-w", "kernel.perf_event_paranoid=0"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode == 0:
                    return 0
            return current_value
        except Exception:
            return 2

    def ensure_upload_disabled(self):
        config_path = Path.home() / ".phoronix-test-suite" / "user-config.xml"
        if not config_path.exists():
            return
        try:
            content = config_path.read_text()
            if "<UploadResults>TRUE</UploadResults>" in content:
                print("  [WARN] UploadResults is TRUE in user-config.xml. Disabling...")
                content = content.replace(
                    "<UploadResults>TRUE</UploadResults>",
                    "<UploadResults>FALSE</UploadResults>",
                )
                config_path.write_text(content)
                print("  [OK] UploadResults set to FALSE")
        except Exception as exc:
            print(f"  [WARN] Failed to check/update user-config.xml: {exc}")

    def ensure_java_available(self):
        java_path = shutil.which("java")
        if not java_path:
            print("[ERROR] java command not found.")
            print("        Provision Java via scripts/setup_jdkxx.sh 17 or scripts_rhel9/setup_jdkxx.sh 17")
            sys.exit(1)

        result = subprocess.run(["java", "-version"], capture_output=True, text=True, check=False)
        if result.returncode != 0:
            print(f"[ERROR] java -version failed: {result.stderr.strip()}")
            sys.exit(1)

        version_line = (result.stderr or result.stdout).strip().splitlines()[0]
        print(f"  [OK] Java detected: {version_line}")

    def patch_dacapo_profile(self):
        """Remove excluded benchmarks from test-definition.xml before scheduling.

        PTS determines the execution list from:
          ~/.phoronix-test-suite/test-profiles/pts/dacapobench-1.1.0/test-definition.xml

        Cassandra is intentionally excluded here because it already exists as a
        standalone PTS benchmark in this repository, and failures inside the
        DaCapo umbrella benchmark should not block the rest of the suite.
        """
        import xml.etree.ElementTree as ET

        profile_dir = Path.home() / ".phoronix-test-suite" / "test-profiles" / "pts" / self.benchmark
        test_def = profile_dir / "test-definition.xml"
        marker = profile_dir / ".dacapo_profile_patched"

        if marker.exists():
            print("  [INFO] patch_dacapo_profile: already patched (marker found)")
            return

        if not test_def.exists():
            print(f"  [WARN] patch_dacapo_profile: {test_def} not found")
            return

        excluded_values = set(self.EXCLUDED_SUBTESTS.keys())
        print("\n>>> Patching dacapobench test-definition.xml to remove excluded benchmarks...")
        print(f"  [INFO] Removing entries with Value in: {excluded_values}")

        tree = ET.parse(test_def)
        root = tree.getroot()

        removed = []
        for option in root.iter("Option"):
            menu = option.find("Menu")
            if menu is None:
                continue
            for entry in list(menu.findall("Entry")):
                value_node = entry.find("Value")
                if value_node is not None and value_node.text in excluded_values:
                    name_node = entry.find("Name")
                    display = name_node.text if name_node is not None else value_node.text
                    menu.remove(entry)
                    removed.append(display)

        if removed:
            tree.write(str(test_def), encoding="utf-8", xml_declaration=True)
            print(f"  [OK] Removed entries: {removed}")
        else:
            print(f"  [INFO] No excluded entries found in {test_def} (may already be absent)")

        marker.write_text("patched\n")
        print(f"  [OK] Marker written: {marker}")

    def patch_dacapo_wrapper(self):
        """Guard installed wrapper against excluded benchmarks as a fallback."""
        installed_dir = Path.home() / ".phoronix-test-suite" / "installed-tests" / "pts" / self.benchmark
        wrapper = installed_dir / "dacapobench"
        marker = installed_dir / ".dacapo_wrapper_patched"

        if marker.exists():
            print("  [INFO] patch_dacapo_wrapper: already patched (marker found)")
            return

        if not wrapper.exists():
            print(f"  [WARN] patch_dacapo_wrapper: {wrapper} not found")
            return

        original = wrapper.read_text()
        lines = original.splitlines()
        shebang_idx = next((i for i, line in enumerate(lines) if line.startswith("#!")), 0)

        case_entries = []
        for jar_name, display_name in self.EXCLUDED_SUBTESTS.items():
            case_entries.append(
                f'    {jar_name})\n'
                f'        echo "[SKIP] dacapobench: \\"{jar_name}\\" excluded by pts_runner ({display_name})"\n'
                f'        echo 0 > ~/test-exit-status\n'
                f'        exit 0\n'
                f'        ;;'
            )

        skip_block = (
            '# Auto-patched by pts_runner: skip excluded benchmarks gracefully\n'
            '_bench="$1"\n'
            'case "$_bench" in\n'
            + "\n".join(case_entries)
            + '\n'
            'esac\n'
        )

        new_lines = lines[: shebang_idx + 1] + ["", skip_block.rstrip()] + lines[shebang_idx + 1 :]
        wrapper.write_text("\n".join(new_lines) + "\n")
        marker.write_text("patched\n")
        print(f"  [OK] Wrapper patched: {wrapper}")

    def install_benchmark(self):
        print(f"\n>>> Installing {self.benchmark_full}...")
        self.preseed.download_from_xml(self.benchmark_full)

        install_cmd = f'phoronix-test-suite batch-install {self.benchmark_full}'
        install_log_env = os.environ.get("PTS_INSTALL_LOG", "").strip().lower()
        install_log_path = os.environ.get("PTS_INSTALL_LOG_PATH", "").strip()
        use_install_log = install_log_env in {"1", "true", "yes"} or bool(install_log_path)
        install_log = Path(install_log_path) if install_log_path else (self.results_dir / "install.log")
        log_file = install_log
        self.results_dir.mkdir(parents=True, exist_ok=True)
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
        output_lines = []
        for line in process.stdout:
            print(line, end="")
            if log_f:
                log_f.write(line)
                log_f.flush()
            output_lines.append(line)
        process.wait()
        if log_f:
            log_f.close()

        returncode = process.returncode
        full_output = "".join(output_lines)
        pts_test_failed, pts_failure_reason = detect_pts_failure_from_log(log_file)
        install_failed = False
        if returncode != 0:
            install_failed = True
        elif pts_test_failed:
            install_failed = True
        elif "Checksum Failed" in full_output:
            install_failed = True
        elif "Downloading of needed test files failed" in full_output:
            install_failed = True

        if install_failed:
            print(f"\n[ERROR] Installation failed with return code {returncode}")
            if pts_failure_reason:
                print(f"        Reason: {pts_failure_reason}")
            for line in output_lines[-20:]:
                print(f"    {line}", end="")
            sys.exit(1)

        verify = subprocess.run(
            ["phoronix-test-suite", "test-installed", self.benchmark_full],
            capture_output=True,
            text=True,
            check=False,
        )
        installed_dir = Path.home() / ".phoronix-test-suite" / "installed-tests" / "pts" / self.benchmark
        if verify.returncode == 0 and installed_dir.exists():
            print("  [OK] Installation verified (PTS recognition + filesystem check)")
        elif verify.returncode == 0:
            print(f"  [WARN] PTS reports installed, but installed-tests directory is missing: {installed_dir}")
        elif installed_dir.exists():
            print(f"  [WARN] installed-tests directory exists, but PTS recognition failed: {installed_dir}")
        else:
            print("  [WARN] Installation verification returned non-zero and installed-tests directory is missing")

        self.patch_dacapo_profile()
        self.patch_dacapo_wrapper()

    def run_benchmark(self, num_threads):
        print(f"\n{'=' * 80}")
        print(f">>> Running DaCapo benchmark with {num_threads} thread(s)")
        print(f"{'=' * 80}")

        self.results_dir.mkdir(parents=True, exist_ok=True)
        log_file = self.results_dir / f"{num_threads}-thread.log"
        stdout_log = self.results_dir / "stdout.log"
        perf_stats_file = self.results_dir / f"{num_threads}-thread_perf_stats.txt"
        freq_start_file = self.results_dir / f"{num_threads}-thread_freq_start.txt"
        freq_end_file = self.results_dir / f"{num_threads}-thread_freq_end.txt"

        quick_env = "FORCE_TIMES_TO_RUN=1 " if self.quick_mode else ""
        sanitized_benchmark = self.benchmark.replace(".", "")
        remove_cmds = [
            f"phoronix-test-suite remove-result {self.benchmark}-{num_threads}threads",
            f"phoronix-test-suite remove-result {sanitized_benchmark}-{num_threads}threads",
        ]
        for cmd in remove_cmds:
            subprocess.run(["bash", "-c", cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

        batch_env = (
            f"{quick_env}BATCH_MODE=1 SKIP_ALL_PROMPTS=1 DISPLAY_COMPACT_RESULTS=1 "
            f"TEST_RESULTS_NAME={self.benchmark}-{num_threads}threads "
            f"TEST_RESULTS_IDENTIFIER={self.benchmark}-{num_threads}threads "
            f"TEST_RESULTS_DESCRIPTION={self.benchmark}-{num_threads}threads"
        )

        if num_threads >= self.vcpu_count:
            cpu_list = ",".join([str(i) for i in range(self.vcpu_count)])
            pts_base_cmd = f"phoronix-test-suite batch-run {self.benchmark_full}"
            cpu_info = f"Using all {num_threads} vCPUs (no taskset)"
        else:
            cpu_list = self.get_cpu_affinity_list(num_threads)
            pts_base_cmd = f"taskset -c {cpu_list} phoronix-test-suite batch-run {self.benchmark_full}"
            cpu_info = f"CPU affinity (taskset): {cpu_list}"

        if self.perf_events:
            if self.perf_paranoid <= 0:
                pts_cmd = (
                    f"NUM_CPU_CORES={num_threads} {batch_env} perf stat -e {self.perf_events} "
                    f"-A -a -o {perf_stats_file} {pts_base_cmd}"
                )
                perf_mode = "Full (per-CPU + HW counters)"
            else:
                pts_cmd = (
                    f"NUM_CPU_CORES={num_threads} {batch_env} perf stat -e {self.perf_events} "
                    f"-o {perf_stats_file} {pts_base_cmd}"
                )
                perf_mode = "Limited (aggregated events only)"
        else:
            pts_cmd = f"NUM_CPU_CORES={num_threads} {batch_env} {pts_base_cmd}"
            perf_mode = "Disabled (perf unavailable)"

        print(f"[INFO] {cpu_info}")
        print(f"[INFO] Perf monitoring mode: {perf_mode}")
        print("[INFO] Recording CPU frequency before benchmark...")
        if self.record_cpu_frequency(freq_start_file):
            print("  [OK] Start frequency recorded")
        else:
            print("  [WARN] CPU frequency not available")

        with open(log_file, "w") as log_f, open(stdout_log, "a") as stdout_f:
            stdout_f.write(f"\n{'=' * 80}\n")
            stdout_f.write(f"[PTS BENCHMARK COMMAND - {num_threads} thread(s)]\n")
            stdout_f.write(f"{pts_cmd}\n")
            stdout_f.write(f"{cpu_info}\n")
            stdout_f.write(f"Perf monitoring: {perf_mode}\n")
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

        print("\n[INFO] Recording CPU frequency after benchmark...")
        if self.record_cpu_frequency(freq_end_file):
            print("  [OK] End frequency recorded")
        else:
            print("  [WARN] CPU frequency not available")

        pts_test_failed, pts_failure_reason = detect_pts_failure_from_log(log_file)
        if returncode == 0 and pts_test_failed:
            print(f"[ERROR] PTS reported benchmark failure despite zero exit code: {pts_failure_reason}")
            return False
        return returncode == 0

    def export_results(self):
        for num_threads in self.thread_list:
            result_name = f"{self.benchmark}-{num_threads}threads"
            result_dir_name = result_name.replace(".", "")

            subprocess.run(
                ["phoronix-test-suite", "result-file-to-csv", result_dir_name],
                capture_output=True,
                text=True,
                check=False,
            )
            home_csv = Path.home() / f"{result_dir_name}.csv"
            if home_csv.exists():
                shutil.move(str(home_csv), str(self.results_dir / f"{num_threads}-thread.csv"))

            subprocess.run(
                ["phoronix-test-suite", "result-file-to-json", result_dir_name],
                capture_output=True,
                text=True,
                check=False,
            )
            home_json = Path.home() / f"{result_dir_name}.json"
            if home_json.exists():
                shutil.move(str(home_json), str(self.results_dir / f"{num_threads}-thread.json"))

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
            with open(json_file, "r") as f:
                data = json.load(f)
            for _, result in data.get("results", {}).items():
                for _, system_result in result.get("results", {}).items():
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

        with open(summary_log, "w") as f:
            f.write("=" * 80 + "\n")
            f.write("DaCapo Benchmark Summary\n")
            f.write(f"Machine: {self.machine_name}\n")
            f.write(f"Test Category: {self.test_category}\n")
            f.write("=" * 80 + "\n\n")

            for result in all_results:
                f.write(f"Threads: {result['threads']}\n")
                f.write(f"  Test: {result['test_name']}\n")
                f.write(f"  Description: {result['description']}\n")
                val_str = f"{result['value']:.2f}" if result["value"] is not None else "FAILED"
                f.write(f"  Average: {val_str} {result['unit']}\n")
                raw_values = result.get("raw_values", [])
                if raw_values:
                    pretty = ", ".join([f"{v:.2f}" for v in raw_values if isinstance(v, (int, float))])
                    f.write(f"  Raw values: {pretty or 'N/A'}\n")
                else:
                    f.write("  Raw values: N/A\n")
                f.write("\n")

            f.write("=" * 80 + "\n")
            f.write("Summary Table\n")
            f.write("=" * 80 + "\n")
            f.write(f"{'Threads':<10} {'Average':<15} {'Unit':<20} {'Test':<30}\n")
            f.write("-" * 80 + "\n")
            for result in all_results:
                val_str = f"{result['value']:<15.2f}" if result["value"] is not None else "FAILED         "
                unit_str = result["unit"] if result["unit"] else ""
                test_name = result["test_name"] if result["test_name"] else ""
                f.write(f"{result['threads']:<10} {val_str} {unit_str:<20} {test_name:<30}\n")

        summary_data = {
            "benchmark": self.benchmark,
            "test_category": self.test_category,
            "machine": self.machine_name,
            "vcpu_count": self.vcpu_count,
            "results": all_results,
        }
        with open(summary_json_file, "w") as f:
            json.dump(summary_data, f, indent=2)

        print(f"[OK] Summary log saved: {summary_log}")
        print(f"[OK] Summary JSON saved: {summary_json_file}")

    def run(self):
        print(f"{'=' * 80}")
        print("DaCapo Benchmark Runner")
        print(f"{'=' * 80}")
        print(f"[INFO] Machine: {self.machine_name}")
        print(f"[INFO] OS: {self.os_name}")
        print(f"[INFO] Benchmark: {self.benchmark_full}")
        print(f"[INFO] Thread list: {self.thread_list}")
        if self.quick_mode:
            print("[INFO] Quick mode enabled: FORCE_TIMES_TO_RUN=1")

        self.results_dir.mkdir(parents=True, exist_ok=True)
        for num_threads in self.thread_list:
            prefix = f"{num_threads}-thread"
            for path in self.results_dir.glob(f"{prefix}*"):
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()

        install_status = get_install_status(self.benchmark_full, self.benchmark)
        info_installed = install_status["info_installed"]
        test_installed_ok = install_status["test_installed_ok"]
        installed_dir_exists = install_status["installed_dir_exists"]
        already_installed = install_status["already_installed"]
        print(
            f"[INFO] Install check -> info:{info_installed}, "
            f"test-installed:{test_installed_ok}, "
            f"dir:{installed_dir_exists}"
        )

        if not already_installed and installed_dir_exists:
            print("[WARN] Existing install directory found but PTS does not report a valid install. Reinstalling.")

        if not already_installed:
            self.install_benchmark()
        else:
            print(f"[INFO] Benchmark already installed, skipping installation: {self.benchmark_full}")
            self.patch_dacapo_profile()
            self.patch_dacapo_wrapper()

        for num_threads in self.thread_list:
            if not self.run_benchmark(num_threads):
                print(f"[WARN] Run failed for {num_threads} thread(s)")

        self.export_results()
        self.generate_summary()
        cleanup_pts_artifacts(self.benchmark)
        return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("threads_pos", nargs="?", type=int)
    parser.add_argument("--threads", type=int)
    parser.add_argument("--quick", action="store_true")
    args = parser.parse_args()

    threads = args.threads if args.threads else args.threads_pos
    if threads is not None and threads <= 0:
        print("[ERROR] Thread count must be > 0")
        sys.exit(1)

    runner = DacapoBenchRunner(threads_arg=threads, quick_mode=args.quick)
    runner.run()


if __name__ == "__main__":
    main()
