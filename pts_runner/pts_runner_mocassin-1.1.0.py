#!/usr/bin/env python3
"""
PTS Runner for mocassin-1.1.0

System Dependencies (from OpenBenchmarking / phoronix-test-suite info):
- Software Dependencies:
  * build-utilities
  * fortran-compiler
  * openmpi-development
- Estimated Install Time: ~2-5 Minutes
- Environment Size: ~56 MB
- Test Type: Processor
- Supported Platforms: Linux, Solaris, BSD

Test Characteristics:
- Multi-threaded: Yes (MPI ranks via mpirun)
- THFix_in_compile: false - Thread count is not embedded in the binary
- THChange_at_runtime: true - Runtime process count is controlled via NUM_CPU_CORES
- Note: Upstream PTS profile uses NUM_CPU_PHYSICAL_CORES in the generated wrapper.
  This runner preserves the project convention via NUM_CPU_CORES while using a
  more portable physical-core-based default for MPI_RANKS / NUM_CPU_PHYSICAL_CORES.
- Measures: Total execution time in Seconds (lower is better)
- App version: Mocassin 2.02.73.3 (test profile version: 1.1.0)

Downloads:
  mocassin-mocassin.2.02.73.3.tar.gz : ~13.6 MB
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from runner_common import detect_pts_failure_from_log, get_install_status, cleanup_pts_artifacts


class PreSeedDownloader:
    """Utility to pre-download benchmark source archives using aria2c if available."""

    def __init__(self, cache_dir=None):
        if cache_dir:
            self.cache_dir = Path(cache_dir)
        else:
            self.cache_dir = Path.home() / ".phoronix-test-suite" / "download-cache"
        self.aria2_available = shutil.which("aria2c") is not None

    def download_from_xml(self, benchmark_name, threshold_mb=64):
        """Parse downloads.xml and accelerate large files via aria2c."""
        if not self.aria2_available:
            return False

        profile_path = (
            Path.home() / ".phoronix-test-suite" / "test-profiles" / benchmark_name / "downloads.xml"
        )
        if not profile_path.exists():
            print(f"  [WARN] downloads.xml not found at {profile_path}")
            try:
                subprocess.run(
                    ["phoronix-test-suite", "info", benchmark_name],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:
                return False
            if not profile_path.exists():
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
                        pass

                if size_bytes > 0 and size_bytes / (1024 * 1024) >= threshold_mb:
                    print(f"  [INFO] {filename} is large, accelerating with aria2c...")
                    self.ensure_file(urls, filename, size_bytes=size_bytes)
        except Exception as exc:
            print(f"  [WARN] Failed to parse downloads.xml: {exc}")
            return False

        return True

    def ensure_file(self, urls, filename, size_bytes=-1):
        """Download file using aria2c with simple size-aware cache validation."""
        target_path = self.cache_dir / filename

        if target_path.exists():
            if size_bytes > 0 and target_path.stat().st_size == size_bytes:
                print(f"  [CACHE] Verified: {filename}")
                return True
            if size_bytes <= 0:
                print(f"  [CACHE] Found: {filename}")
                return True

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        num_conn = 16 if size_bytes < 10 * 1024 * 1024 * 1024 else 4
        cmd = [
            "aria2c",
            "-x",
            str(num_conn),
            "-s",
            str(num_conn),
            "--continue=true",
            "-d",
            str(self.cache_dir),
            "-o",
            filename,
        ] + list(urls)

        try:
            subprocess.run(cmd, check=True)
            return True
        except subprocess.CalledProcessError as exc:
            print(f"  [WARN] aria2c download failed for {filename}: {exc}")
            return False


class MocassinRunner:
    def __init__(self, threads_arg=None, quick_mode=False):
        """
        Initialize Mocassin benchmark runner.

        Args:
            threads_arg: Fixed thread count. None = 4-point scaling [n/4, n/2, 3n/4, n].
            quick_mode: If True, run each test once (FORCE_TIMES_TO_RUN=1) for development.
        """
        self.benchmark = "mocassin-1.1.0"
        self.benchmark_full = f"pts/{self.benchmark}"
        self.test_category = "FPU"
        self.test_category_dir = self.test_category.replace(" ", "_")

        self.vcpu_count = os.cpu_count() or 1
        self.physical_core_count = self.detect_physical_core_count()
        self.machine_name = os.environ.get("MACHINE_NAME", os.uname().nodename)
        self.os_name = self.get_os_name()
        self.arch = os.uname().machine

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
            with open("/etc/os-release", "r") as f:
                lines = f.readlines()
            info = {}
            for line in lines:
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    info[k] = v.strip('"')
            if "NAME" in info and "VERSION_ID" in info:
                return f"{info['NAME'].split()[0]}_{info['VERSION_ID'].replace('.', '_')}"
        except Exception:
            pass

        return "Unknown_OS"

    def is_wsl(self):
        """Detect if running in WSL environment."""
        try:
            if not os.path.exists("/proc/version"):
                return False
            with open("/proc/version", "r") as f:
                content = f.read().lower()
                return "microsoft" in content or "wsl" in content
        except Exception:
            return False

    def get_cpu_frequencies(self):
        """Get current CPU frequencies (cross-platform)."""
        frequencies = []

        try:
            result = subprocess.run(
                ["bash", "-c", 'grep "cpu MHz" /proc/cpuinfo'],
                capture_output=True,
                text=True,
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

        try:
            freq_files = sorted(Path("/sys/devices/system/cpu").glob("cpu[0-9]*/cpufreq/scaling_cur_freq"))
            if not freq_files:
                freq_files = sorted(Path("/sys/devices/system/cpu").glob("cpu[0-9]*/cpufreq/cpuinfo_cur_freq"))
            for freq_file in freq_files:
                try:
                    with open(freq_file, "r") as f:
                        frequencies.append(int(f.read().strip()))
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
                            mhz = float(parts[1].strip().replace(",", "."))
                            return [int(mhz * 1000)] * self.vcpu_count
        except Exception:
            pass

        return frequencies

    def record_cpu_frequency(self, output_file):
        """Record CPU frequencies to file."""
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
        """Determine available perf events (3-stage fallback)."""
        perf_path = shutil.which("perf")
        if not perf_path:
            print("  [INFO] perf command not found in PATH")
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
        except Exception:
            pass

        sw_events = "cpu-clock,task-clock,context-switches,cpu-migrations"
        try:
            result = subprocess.run(
                ["bash", "-c", f"{perf_path} stat -e {sw_events} sleep 0.01 2>&1"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if result.returncode == 0:
                return sw_events
        except Exception:
            pass

        return None

    def check_and_setup_perf_permissions(self):
        """Check perf_event_paranoid and adjust if needed."""
        try:
            result = subprocess.run(
                ["cat", "/proc/sys/kernel/perf_event_paranoid"],
                capture_output=True,
                text=True,
                check=True,
            )
            current_value = int(result.stdout.strip())
            if current_value >= 1:
                result = subprocess.run(
                    ["sudo", "sysctl", "-w", "kernel.perf_event_paranoid=0"],
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    return 0
            return current_value
        except Exception:
            return 2

    def ensure_upload_disabled(self):
        """Ensure PTS results upload is disabled."""
        config_path = Path.home() / ".phoronix-test-suite" / "user-config.xml"
        if not config_path.exists():
            return
        try:
            with open(config_path, "r") as f:
                content = f.read()
            if "<UploadResults>TRUE</UploadResults>" in content:
                content = content.replace("<UploadResults>TRUE</UploadResults>", "<UploadResults>FALSE</UploadResults>")
                with open(config_path, "w") as f:
                    f.write(content)
                print("  [OK] UploadResults set to FALSE")
        except Exception as exc:
            print(f"  [WARN] Failed to update user-config.xml: {exc}")

    def clean_pts_cache(self):
        """Clean PTS installed tests for fresh installation."""
        installed_dir = Path.home() / ".phoronix-test-suite" / "installed-tests" / "pts" / self.benchmark
        if installed_dir.exists():
            print(f"  [CLEAN] Removing installed test: {installed_dir}")
            shutil.rmtree(installed_dir)
        print("  [OK] PTS cache cleaned")

    def get_cpu_affinity_list(self, n):
        """
        Generate CPU affinity list preferring physical cores first.

        This matches the convention used by other CPU-bound runners in this repo.
        """
        half = self.vcpu_count // 2
        cpu_list = []

        if n <= half:
            cpu_list = [str(i * 2) for i in range(n)]
        else:
            cpu_list = [str(i * 2) for i in range(half)]
            logical_count = n - half
            cpu_list.extend([str(i * 2 + 1) for i in range(logical_count)])

        return ",".join(cpu_list)

    def detect_physical_core_count(self):
        """Best-effort physical core detection with conservative fallbacks."""
        try:
            result = subprocess.run(
                ["lscpu", "-p=CORE,SOCKET"],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0 and result.stdout:
                core_socket_pairs = set()
                for line in result.stdout.splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 2 and parts[0] and parts[1]:
                        core_socket_pairs.add((parts[0], parts[1]))
                if core_socket_pairs:
                    return len(core_socket_pairs)
        except Exception:
            pass

        try:
            physical_ids = set()
            current_physical = None
            current_core = None
            with open("/proc/cpuinfo", "r") as f:
                for line in f:
                    if not line.strip():
                        if current_physical is not None and current_core is not None:
                            physical_ids.add((current_physical, current_core))
                        current_physical = None
                        current_core = None
                        continue
                    if ":" not in line:
                        continue
                    key, value = [x.strip() for x in line.split(":", 1)]
                    if key == "physical id":
                        current_physical = value
                    elif key == "core id":
                        current_core = value
            if current_physical is not None and current_core is not None:
                physical_ids.add((current_physical, current_core))
            if physical_ids:
                return len(physical_ids)
        except Exception:
            pass

        if self.vcpu_count % 2 == 0 and self.vcpu_count > 1:
            return max(1, self.vcpu_count // 2)
        return self.vcpu_count

    def get_visible_physical_core_count(self, num_threads):
        """Estimate physical cores represented by the selected visible CPUs."""
        return max(1, min(num_threads, self.physical_core_count))

    def get_compiler_env(self):
        """Return compiler environment, preferring GCC 14 / GFortran 14 when available."""
        cc = "gcc-14" if shutil.which("gcc-14") else "gcc"
        cxx = "g++-14" if shutil.which("g++-14") else "g++"
        fc = "gfortran-14" if shutil.which("gfortran-14") else "gfortran"
        return cc, cxx, fc

    def fetch_test_profile(self):
        """Ensure the PTS profile exists locally before patching."""
        try:
            subprocess.run(
                ["bash", "-c", f"phoronix-test-suite info {self.benchmark_full}"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            print(f"  [WARN] Failed to fetch test profile metadata: {exc}")

    def patch_install_script(self):
        """
        Patch PTS install.sh for this project's execution model.

        Upstream install.sh currently:
          * generates a wrapper using mpirun -np $NUM_CPU_PHYSICAL_CORES
          * creates input/output directories with plain mkdir
          * builds with plain "make"

        This runner patches it to:
          * keep NUM_CPU_CORES for project-wide runtime scaling metadata
          * drive mpirun rank count from MPI_RANKS first, then NUM_CPU_PHYSICAL_CORES
          * make install reruns idempotent via mkdir -p for input/output
        """
        install_sh_path = (
            Path.home() / ".phoronix-test-suite" / "test-profiles" / "pts" / self.benchmark / "install.sh"
        )
        if not install_sh_path.exists():
            print(f"  [WARN] install.sh not found at {install_sh_path}")
            return False

        try:
            content = install_sh_path.read_text()
            original_content = content
            patched = False

            np_replacements = [
                (
                    r"-np \$NUM_CPU_PHYSICAL_CORES",
                    r"-np \${MPI_RANKS:-\${NUM_CPU_PHYSICAL_CORES:-1}}",
                ),
                (
                    r"-np \${NUM_CPU_CORES:-\${NUM_CPU_PHYSICAL_CORES:-1}}",
                    r"-np \${MPI_RANKS:-\${NUM_CPU_PHYSICAL_CORES:-1}}",
                ),
            ]
            for old_np, new_np in np_replacements:
                if old_np in content:
                    content = content.replace(old_np, new_np)
                    patched = True

            replacements = [
                ("mkdir input", "mkdir -p input"),
                ("mkdir output", "mkdir -p output"),
                ("make\n", "make -j ${NUM_CPU_CORES:-1}\n"),
            ]
            for old_text, new_text in replacements:
                if old_text in content:
                    content = content.replace(old_text, new_text)
                    patched = True

            if patched:
                install_sh_path.write_text(content if content.endswith("\n") else content + "\n")
                print("  [OK] install.sh patched for portable MPI rank scaling and idempotent Mocassin setup")
                return True

            if original_content == content:
                print("  [INFO] install.sh already patched or no patch needed")
            return True
        except Exception as exc:
            print(f"  [ERROR] Failed to patch install.sh: {exc}")
            return False

    def install_benchmark(self):
        """Install Mocassin with source prefetch and install.sh patching."""
        print("\n>>> Checking for downloadable source archives...")
        self.fetch_test_profile()
        downloader = PreSeedDownloader()
        downloader.download_from_xml(self.benchmark_full, threshold_mb=8)

        print(f"\n>>> Installing {self.benchmark_full}...")
        print("  [INFO] Removing existing installation...")
        remove_cmd = f'echo "y" | phoronix-test-suite remove-installed-test "{self.benchmark_full}"'
        subprocess.run(["bash", "-c", remove_cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        self.fetch_test_profile()
        self.patch_install_script()

        cc, cxx, fc = self.get_compiler_env()
        nproc = os.cpu_count() or 1
        install_physical = max(1, min(self.physical_core_count, nproc))
        
        mpi_path_setup = (
            "export PATH=/usr/lib64/openmpi/bin:/usr/lib64/mpich/bin:$PATH; "
            "export LD_LIBRARY_PATH=/usr/lib64/openmpi/lib:/usr/lib64/mpich/lib:$LD_LIBRARY_PATH; "
            "export PKG_CONFIG_PATH=/usr/lib64/openmpi/lib/pkgconfig:/usr/lib64/mpich/lib/pkgconfig:$PKG_CONFIG_PATH; "
        )
        
        install_cmd = (
            f'{mpi_path_setup}'
            f'NUM_CPU_CORES={nproc} '
            f'NUM_CPU_PHYSICAL_CORES={install_physical} '
            f'MPI_RANKS={install_physical} '
            f'CC={cc} CXX={cxx} FC={fc} '
            f'MAKEFLAGS="-j{nproc}" '
            f'phoronix-test-suite batch-install {self.benchmark_full}'
        )

        print(f"\n{'>'*80}")
        print("[PTS INSTALL COMMAND]")
        print(f"  {install_cmd}")
        print(f"{'<'*80}\n")

        install_log_env = os.environ.get("PTS_INSTALL_LOG", "").strip().lower()
        install_log_path = os.environ.get("PTS_INSTALL_LOG_PATH", "").strip()
        use_install_log = install_log_env in {"1", "true", "yes"} or bool(install_log_path)
        install_log = Path(install_log_path) if install_log_path else (self.results_dir / "install.log")
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

        install_output = []
        for line in process.stdout:
            print(line, end="")
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

        full_output = "".join(install_output)
        install_failed = False
        if returncode != 0:
            install_failed = True
        elif pts_test_failed:
            install_failed = True
        elif "Checksum Failed" in full_output or "Downloading of needed test files failed" in full_output:
            install_failed = True
        elif "installer exited with a non-zero exit status" in full_output.lower():
            install_failed = True

        if install_failed:
            print(f"\n  [ERROR] Installation failed (returncode={returncode})")
            if pts_test_failed:
                print(f"  [ERROR] PTS failure: {pts_failure_reason}")
            sys.exit(1)

        installed_dir = Path.home() / ".phoronix-test-suite" / "installed-tests" / "pts" / self.benchmark
        if not installed_dir.exists():
            print(f"  [ERROR] Installation directory not found: {installed_dir}")
            sys.exit(1)

        print(f"  [OK] Installation completed: {installed_dir}")

    def run_benchmark(self, num_threads):
        """Run Mocassin with taskset-based affinity and NUM_CPU_CORES-driven MPI scaling."""
        self.results_dir.mkdir(parents=True, exist_ok=True)
        log_file = self.results_dir / f"{num_threads}-thread.log"
        stdout_log = self.results_dir / "stdout.log"
        perf_stats_file = self.results_dir / f"{num_threads}-thread_perf_stats.txt"
        freq_start_file = self.results_dir / f"{num_threads}-thread_freq_start.txt"
        freq_end_file = self.results_dir / f"{num_threads}-thread_freq_end.txt"
        perf_summary_file = self.results_dir / f"{num_threads}-thread_perf_summary.json"

        if num_threads >= self.vcpu_count:
            cpu_list = ",".join([str(i) for i in range(self.vcpu_count)])
            pts_base_cmd = f"phoronix-test-suite batch-run {self.benchmark_full}"
            cpu_info = f"Using all {num_threads} vCPUs (no taskset)"
        else:
            cpu_list = self.get_cpu_affinity_list(num_threads)
            pts_base_cmd = f"taskset -c {cpu_list} phoronix-test-suite batch-run {self.benchmark_full}"
            cpu_info = f"CPU affinity (taskset): {cpu_list}"

        sanitized_benchmark = self.benchmark.replace(".", "")
        for cmd in [
            f"phoronix-test-suite remove-result {self.benchmark}-{num_threads}threads",
            f"phoronix-test-suite remove-result {sanitized_benchmark}-{num_threads}threads",
        ]:
            subprocess.run(["bash", "-c", cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        quick_env = "FORCE_TIMES_TO_RUN=1 " if self.quick_mode else ""
        batch_env = (
            f"{quick_env}"
            f"BATCH_MODE=1 SKIP_ALL_PROMPTS=1 DISPLAY_COMPACT_RESULTS=1 "
            f"TEST_RESULTS_NAME={self.benchmark}-{num_threads}threads "
            f"TEST_RESULTS_IDENTIFIER={self.benchmark}-{num_threads}threads "
            f"TEST_RESULTS_DESCRIPTION={self.benchmark}-{num_threads}threads"
        )

        visible_physical = self.get_visible_physical_core_count(num_threads)
        mpi_ranks = visible_physical
        
        mpi_path_setup = (
            "export PATH=/usr/lib64/openmpi/bin:/usr/lib64/mpich/bin:$PATH; "
            "export LD_LIBRARY_PATH=/usr/lib64/openmpi/lib:/usr/lib64/mpich/lib:$LD_LIBRARY_PATH; "
        )
        
        base_env_prefix = (
            f"{mpi_path_setup}"
            f"NUM_CPU_CORES={num_threads} "
            f"NUM_CPU_PHYSICAL_CORES={visible_physical} "
            f"MPI_RANKS={mpi_ranks} "
        )
        if self.perf_events:
            if self.perf_paranoid <= 0:
                perf_cmd = f"perf stat -e {self.perf_events} -A -a -o {perf_stats_file}"
            else:
                perf_cmd = f"perf stat -e {self.perf_events} -o {perf_stats_file}"
            pts_cmd = f"{base_env_prefix}{batch_env} {perf_cmd} {pts_base_cmd}"
        else:
            pts_cmd = f"{base_env_prefix}{batch_env} {pts_base_cmd}"

        print(f"  [INFO] {cpu_info}")
        print(f"  [INFO] Physical core baseline: {self.physical_core_count}")
        print(f"  [INFO] Visible physical cores for this run: {visible_physical}")
        print(f"  [INFO] MPI rank count target: {mpi_ranks}")

        self.record_cpu_frequency(freq_start_file)

        with open(log_file, "w") as log_f, open(stdout_log, "a") as stdout_f:
            stdout_f.write(f"\n{'='*80}\n")
            stdout_f.write(f"[PTS BENCHMARK COMMAND - {num_threads} thread(s)]\n")
            stdout_f.write(f"{pts_cmd}\n")
            stdout_f.write(f"{'='*80}\n\n")
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

        self.record_cpu_frequency(freq_end_file)
        pts_test_failed, pts_failure_reason = detect_pts_failure_from_log(log_file)

        if returncode == 0 and not pts_test_failed:
            print("\n[OK] Benchmark completed successfully")
            if self.perf_events and perf_stats_file.exists():
                try:
                    with open(perf_summary_file, "w") as f:
                        json.dump({"perf_stats_file": str(perf_stats_file), "cpu_list": cpu_list}, f, indent=2)
                except Exception as exc:
                    print(f"  [WARN] Failed to save perf summary: {exc}")
            return True

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
            result_dir_name = result_name.replace(".", "")
            result_dir = pts_results_dir / result_dir_name

            if not result_dir.exists():
                print(f"  [WARN] Result not found: {result_dir}")
                continue

            csv_output = self.results_dir / f"{num_threads}-thread.csv"
            result = subprocess.run(
                ["phoronix-test-suite", "result-file-to-csv", result_dir_name],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                home_csv = Path.home() / f"{result_dir_name}.csv"
                if home_csv.exists():
                    shutil.move(str(home_csv), str(csv_output))
                    print(f"  [OK] Saved: {csv_output}")

            json_output = self.results_dir / f"{num_threads}-thread.json"
            result = subprocess.run(
                ["phoronix-test-suite", "result-file-to-json", result_dir_name],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                home_json = Path.home() / f"{result_dir_name}.json"
                if home_json.exists():
                    shutil.move(str(home_json), str(json_output))
                    print(f"  [OK] Saved: {json_output}")

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
                    with open(json_file, "r") as f:
                        data = json.load(f)
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
                except Exception as exc:
                    print(f"  [WARN] Failed to parse {json_file}: {exc}")

        if not all_results:
            print("[WARN] No results found for summary generation")
            return

        with open(summary_log, "w") as f:
            f.write("=" * 80 + "\n")
            f.write(f"Benchmark Summary: {self.benchmark}\n")
            f.write(f"Machine: {self.machine_name}\n")
            f.write(f"Test Category: {self.test_category}\n")
            f.write("=" * 80 + "\n\n")
            f.write(f"{'Threads':<10} {'Test':<35} {'Value':>12} {'Unit':<10}\n")
            f.write("-" * 70 + "\n")
            for result in all_results:
                val_str = f"{result['value']:.2f}" if result["value"] is not None else "FAILED    "
                desc = result.get("description") or result.get("test_name") or ""
                f.write(f"{result['threads']:<10} {str(desc):<35} {val_str:>12} {str(result['unit'] or ''):<10}\n")

        print(f"[OK] Summary log saved: {summary_log}")

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

    def run(self):
        """Main execution method."""
        print(f"{'='*80}")
        print(f"PTS Benchmark Runner: {self.benchmark}")
        print(f"Machine: {self.machine_name}")
        print(f"OS: {self.os_name}")
        print(f"Arch: {self.arch}")
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
        installed_dir_exists = install_status["installed_dir_exists"]
        already_installed = install_status["already_installed"]

        print(
            f"[INFO] Install check -> info:{install_status['info_installed']}, "
            f"test-installed:{install_status['test_installed_ok']}, dir:{install_status['installed_dir_exists']}"
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

        print(f"\n{'='*80}")
        print("Benchmark Summary")
        print(f"{'='*80}")
        print(f"Total tests:  {len(self.thread_list)}")
        print(f"Successful:   {len(self.thread_list) - len(failed)}")
        print(f"Failed:       {len(failed)}")
        if failed:
            print(f"Failed thread counts: {failed}")
        print(f"{'='*80}")

        return len(failed) == 0


def main():
    parser = argparse.ArgumentParser(
        description="PTS Runner for mocassin-1.1.0 (Monte Carlo Simulations of Ionised Nebulae)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "threads_pos",
        nargs="?",
        type=int,
        help="Number of threads (optional positional, omit for 4-point scaling mode)",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=None,
        help="Fixed thread count. Default: 4-point scaling [n/4, n/2, 3n/4, n]",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Quick mode: run each test once (FORCE_TIMES_TO_RUN=1)",
    )
    args = parser.parse_args()

    threads = args.threads if args.threads is not None else args.threads_pos

    runner = MocassinRunner(threads_arg=threads, quick_mode=args.quick)
    success = runner.run()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
