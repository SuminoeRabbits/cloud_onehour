#!/usr/bin/env python3
"""
PTS Runner for spark-1.0.1

System Dependencies (from phoronix-test-suite info):
- Software Dependencies:
  * Java
  * Python
- Estimated Install Time: 7 Seconds
- Environment Size: 2100 MB
- Test Type: System
- Supported Platforms: Linux

Test Characteristics:
- Multi-threaded: Yes (Spark manages parallelism internally)
- Honors CFLAGS/CXXFLAGS: N/A (Java/Python-based)
- Notable Instructions: SVE2 support via JVM (OpenJDK 9+)
- THFix_in_compile: false - Thread count NOT fixed at compile time
- THChange_at_runtime: true - Spark auto-detects cores, configurable via spark-defaults.conf
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

    def download_from_xml(self, benchmark_name, threshold_mb=96):
        """
        Parse downloads.xml for the benchmark and download large files.

        Args:
            benchmark_name: Full benchmark name (e.g., "pts/spark-1.0.1")
            threshold_mb: Size threshold in MB to trigger aria2c (default: 96MB)
        """
        if not self.aria2_available:
            return False

        # Locate downloads.xml
        profile_path = Path.home() / ".phoronix-test-suite" / "test-profiles" / benchmark_name / "downloads.xml"

        if not profile_path.exists():
            print(f"  [WARN] downloads.xml not found at {profile_path}")
            print(f"  [INFO] Attempting to fetch test profile via phoronix-test-suite info {benchmark_name}...")
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

                # Determine size
                size_bytes = -1
                if filesize_node is not None and filesize_node.text:
                    try:
                        size_bytes = int(filesize_node.text.strip())
                    except ValueError:
                        pass

                # If size not in XML, try to get it from network
                if size_bytes <= 0:
                    size_bytes = self.get_remote_file_size(url)

                # Check threshold
                if size_bytes > 0:
                    size_mb = size_bytes / (1024 * 1024)
                    if size_mb >= threshold_mb:
                        print(f"  [INFO] {filename} is large ({size_mb:.1f} MB), accelerating with aria2c...")
                        self.ensure_file(url, filename)

        except Exception as e:
            print(f"  [ERROR] Failed to parse downloads.xml: {e}")
            return False

        return True

    def get_remote_file_size(self, url):
        """
        Get remote file size in bytes using curl.
        Returns -1 if size cannot be determined.
        """
        try:
            cmd = ['curl', '-s', '-I', '-L', url]
            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode != 0:
                return -1

            for line in result.stdout.splitlines():
                if line.lower().startswith('content-length:'):
                    try:
                        size_str = line.split(':')[1].strip()
                        return int(size_str)
                    except ValueError:
                        pass
        except Exception:
            pass

        return -1

    def ensure_file(self, url, filename):
        """
        Directly download file using aria2c.
        """
        target_path = self.cache_dir / filename

        if target_path.exists():
            print(f"  [CACHE] File found: {filename}")
            return True

        print(f"  [ARIA2] Downloading {filename} with 16 connections...")
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            "aria2c", "-x", "16", "-s", "16",
            "-d", str(self.cache_dir),
            "-o", filename,
            url
        ]

        try:
            subprocess.run(cmd, check=True)
            print(f"  [OK] Download completed: {filename}")
            return True
        except subprocess.CalledProcessError:
            print("  [WARN] aria2c download failed, falling back to PTS default")
            if target_path.exists():
                target_path.unlink()
            return False


class SparkRunner:
    def __init__(self, threads_arg=None, quick_mode=False):
        """
        Initialize Spark runner.

        Args:
            threads_arg: Thread count argument (None for scaling mode, int for fixed mode)
            quick_mode: If True, run tests once (FORCE_TIMES_TO_RUN=1) for development
        """
        self.benchmark = "spark-1.0.1"
        self.benchmark_full = f"pts/{self.benchmark}"
        self.test_category = "Network"
        # Replace spaces with underscores in test_category for directory name
        self.test_category_dir = self.test_category.replace(" ", "_")

        # System info
        self.vcpu_count = os.cpu_count() or 1
        self.machine_name = os.environ.get('MACHINE_NAME', os.uname().nodename)
        self.os_name = self.get_os_name()
        
        # Python version info for compatibility checks
        self.py_version = sys.version_info
        print(f"  [INFO] Running on Python {self.py_version.major}.{self.py_version.minor}.{self.py_version.micro}")

        # Python 3.12+ compatibility: prefer older Python for Spark 3.3
        self.spark_python_exec = None
        if self.py_version >= (3, 12):
            for candidate in ("python3.11", "python3.10"):
                if shutil.which(candidate):
                    self.spark_python_exec = candidate
                    break
            if self.spark_python_exec:
                print(f"  [INFO] Spark compatibility: using {self.spark_python_exec}")
            else:
                print("  [WARN] Python >=3.12 detected but no python3.11/3.10 found; Spark may fail")

        # Python 3.12+ compatibility: store patched Spark python directory path
        # This will be set by fix_benchmark_specific_issues() if patching is needed
        self.spark_python_dir = None

        # Determine thread execution mode
        if threads_arg is None:
            # Even-number scaling: [2, 4, 6, ..., nproc]
            # 4-point scaling: [nproc/4, nproc/2, nproc*3/4, nproc]

            n_4 = self.vcpu_count // 4

            self.thread_list = [n_4, n_4 * 2, n_4 * 3, self.vcpu_count]

            # Remove any zeros and deduplicate

            self.thread_list = sorted(list(set([t for t in self.thread_list if t > 0])))
        else:
            # Fixed mode: single thread count
            n = min(threads_arg, self.vcpu_count)
            self.thread_list = [n]

        # Project structure
        self.script_dir = Path(__file__).parent.resolve()
        self.project_root = self.script_dir.parent
        self.results_dir = self.project_root / "results" / self.machine_name / self.os_name / self.test_category_dir / self.benchmark

        # Quick mode for development
        self.quick_mode = quick_mode

        # Detect environment for logging
        self.is_wsl_env = self.is_wsl()
        if self.is_wsl_env:
            print("  [INFO] Running on WSL environment")

        # Enforce safety
        self.ensure_upload_disabled()

        # IMPORTANT: Setup perf permissions BEFORE testing perf availability
        # This allows perf to work on cloud VMs with restrictive defaults
        self.perf_paranoid = self.check_and_setup_perf_permissions()

        # Feature Detection: Check if perf is actually functional
        # This must be called AFTER check_and_setup_perf_permissions()
        self.perf_events = self.get_perf_events()
        if self.perf_events:
            print(f"  [OK] Perf monitoring enabled with events: {self.perf_events}")
        else:
            print("  [INFO] Perf monitoring disabled (command missing or unsupported)")

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

        Returns:
            bool: True if running in WSL, False otherwise
        """
        try:
            if not os.path.exists('/proc/version'):
                return False
            with open('/proc/version', 'r') as f:
                content = f.read().lower()
                return 'microsoft' in content or 'wsl' in content
        except Exception:
            return False
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
            str: Comma-separated list of available perf events, or None if perf unavailable
        """
        perf_path = shutil.which("perf")
        if not perf_path:
            print("  [INFO] perf command not found")
            return None

        # Test 1: Try hardware + software events
        hw_events = "cycles,instructions,cpu-clock,task-clock,context-switches,cpu-migrations"
        test_cmd = f"perf stat -e {hw_events} -- sleep 0.01"
        result = subprocess.run(
            ['bash', '-c', test_cmd],
            capture_output=True,
            text=True
        )

        if result.returncode == 0:
            # Check if output contains error about unsupported events
            combined_output = result.stderr + result.stdout
            if 'not supported' not in combined_output.lower() and 'not counted' not in combined_output.lower():
                return hw_events

        # Test 2: Try software-only events
        sw_events = "cpu-clock,task-clock,context-switches,cpu-migrations"
        test_cmd = f"perf stat -e {sw_events} -- sleep 0.01"
        result = subprocess.run(
            ['bash', '-c', test_cmd],
            capture_output=True,
            text=True
        )

        if result.returncode == 0:
            combined_output = result.stderr + result.stdout
            if 'not supported' not in combined_output.lower() and 'not counted' not in combined_output.lower():
                return sw_events

        # Test 3: perf unavailable
        print("  [WARN] perf events not available")
        return None

    def check_and_setup_perf_permissions(self):
        """
        Check perf_event_paranoid setting and adjust if needed.

        Returns:
            int: Current perf_event_paranoid value after adjustment
        """
        print(f"\n{'='*80}")
        print(">>> Checking perf_event_paranoid setting")
        print(f"{'='*80}")

        try:
            # Read current setting
            result = subprocess.run(
                ['cat', '/proc/sys/kernel/perf_event_paranoid'],
                capture_output=True,
                text=True,
                check=True
            )
            current_value = int(result.stdout.strip())

            print(f"  [INFO] Current perf_event_paranoid: {current_value}")

            # If too restrictive, try to adjust
            if current_value >= 1:
                print(f"  [WARN] perf_event_paranoid={current_value} is too restrictive for system-wide monitoring")
                print("  [INFO] Attempting to adjust perf_event_paranoid to 0...")

                result = subprocess.run(
                    ['sudo', 'sysctl', '-w', 'kernel.perf_event_paranoid=0'],
                    capture_output=True,
                    text=True
                )

                if result.returncode == 0:
                    print("  [OK] perf_event_paranoid adjusted to 0 (temporary, until reboot)")
                    return 0
                else:
                    print("  [ERROR] Failed to adjust perf_event_paranoid (sudo required)")
                    print("  [WARN] Running in LIMITED mode")
                    return current_value
            else:
                print(f"  [OK] perf_event_paranoid={current_value} is acceptable")
                return current_value

        except Exception as e:
            print(f"  [ERROR] Could not check perf_event_paranoid: {e}")
            print("  [WARN] Assuming restrictive mode (perf_event_paranoid=2)")
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
        """Clean PTS installed tests for fresh installation."""
        print(">>> Cleaning PTS cache...")

        pts_home = Path.home() / '.phoronix-test-suite'

        # NOTE: Do NOT clean test profiles - they may contain manual fixes for checksum issues
        # Only clean installed tests to force fresh compilation

        # Clean installed tests
        installed_dir = pts_home / 'installed-tests' / 'pts' / self.benchmark
        if installed_dir.exists():
            print(f"  [CLEAN] Removing installed test: {installed_dir}")
            shutil.rmtree(installed_dir)

        print("  [OK] PTS cache cleaned")

    def get_cpu_affinity_list(self, n):
        """
        Generate CPU affinity list for HyperThreading optimization.

        Args:
            n: Number of threads

        Returns:
            Comma-separated CPU list string (e.g., "0,2,4,1,3")
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
        Install spark-1.0.1 with GCC-14 native compilation.

        Note: Spark uses JVM and auto-detects CPU cores.
        Since THFix_in_compile=false, NUM_CPU_CORES is NOT set during build.
        """
        print(f"\n>>> Installing {self.benchmark_full}...")

        # Pre-download large files with aria2c for speed
        print("\n>>> Checking for large files to pre-seed...")
        downloader = PreSeedDownloader()
        downloader.download_from_xml(self.benchmark_full, threshold_mb=96)

        # Remove existing installation first
        print("  [INFO] Removing existing installation...")
        remove_cmd = f'echo "y" | phoronix-test-suite remove-installed-test "{self.benchmark_full}"'
        print(f"  [INSTALL CMD] {remove_cmd}")
        subprocess.run(
            ['bash', '-c', remove_cmd],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        # Build install command
        nproc = os.cpu_count() or 1
        install_cmd = f'MAKEFLAGS="-j{nproc}" CC=gcc-14 CXX=g++-14 CFLAGS="-O3 -march=native -mtune=native" CXXFLAGS="-O3 -march=native -mtune=native" phoronix-test-suite batch-install {self.benchmark_full}'

        # Print install command for debugging
        print(f"\n{'>'*80}")
        print("[PTS INSTALL COMMAND]")
        print(f"  {install_cmd}")
        print(f"{'<'*80}\n")        # Execute install command with real-time output streaming
        print("  Running installation...")
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

        # Check for installation failure
        install_failed = False
        full_output = ''.join(install_output)

        if returncode != 0:
            install_failed = True
        elif pts_test_failed:
            install_failed = True
        elif 'Checksum Failed' in full_output or 'Downloading of needed test files failed' in full_output:
            install_failed = True
        elif 'ERROR' in full_output or 'FAILED' in full_output:
            install_failed = True

        if install_failed:
            print(f"  [ERROR] Installation failed with return code {returncode}")
            print("  [INFO] Check output above for details")
            if use_install_log:
                print(f"  [INFO] Install log: {install_log}")
            sys.exit(1)

        # Verify installation by checking if directory exists
        pts_home = Path.home() / '.phoronix-test-suite'
        installed_dir = pts_home / 'installed-tests' / 'pts' / self.benchmark

        if not installed_dir.exists():
            print("  [ERROR] Installation verification failed")
            print(f"  [ERROR] Expected directory not found: {installed_dir}")
            print("  [INFO] Installation may have failed silently")
            print(f"  [INFO] Try manually installing: phoronix-test-suite install {self.benchmark_full}")
            sys.exit(1)

        # Check if test is recognized by PTS
        verify_cmd = f'phoronix-test-suite test-installed {self.benchmark_full}'
        verify_result = subprocess.run(
            ['bash', '-c', verify_cmd],
            capture_output=True,
            text=True
        )

        if verify_result.returncode != 0:
            print("  [WARN] Test may not be fully installed (test-installed check failed)")
            print("  [INFO] But installation directory exists, continuing...")

        print(f"  [OK] Installation completed and verified: {installed_dir}")

        # Patch spark execution script to handle missing $LOG_FILE and bad paths
        print("\n  [INFO] Patching spark execution script...")
        spark_script = installed_dir / 'spark'
        if spark_script.exists():
            with open(spark_script, 'r') as f:
                script_content = f.read()

            # Replace $LOG_FILE redirects with safe fallback to stdout
            # Keep LOG_FILE when PTS sets it, otherwise use /dev/stdout
            patched_content = script_content.replace(
                '> $LOG_FILE 2>&1',
                '> "${LOG_FILE:-/dev/stdout}" 2>&1'
            ).replace(
                '>> $LOG_FILE 2>&1',
                '>> "${LOG_FILE:-/dev/stdout}" 2>&1'
            )

            # Fix malformed redirect that writes to a file named "2>&1"
            patched_content = patched_content.replace('>2>&1', '2>&1')

            # Fix absolute $HOME paths to use installed test directory
            patched_content = patched_content.replace(
                'cd ~/spark-3.3.0-bin-hadoop3/bin',
                'cd spark-3.3.0-bin-hadoop3/bin'
            )
            patched_content = patched_content.replace(
                '$HOME/pyspark-benchmark',
                '../../pyspark-benchmark'
            )
            patched_content = patched_content.replace(
                '$HOME/test-data',
                '../../test-data'
            )

            # Ensure spark-submit lines write into LOG_FILE even if it was removed previously
            lines = patched_content.splitlines()
            new_lines = []
            seen_submit = 0
            for line in lines:
                if 'spark-submit' in line and 'LOG_FILE' not in line:
                    clean_line = re.sub(r'\s*(>>?|)\s*[^\\s]*\s*2>&1\s*$', '', line).rstrip()
                    redir = '> "${LOG_FILE:-/dev/stdout}" 2>&1' if seen_submit == 0 else '>> "${LOG_FILE:-/dev/stdout}" 2>&1'
                    line = f"{clean_line} {redir}"
                    seen_submit += 1
                new_lines.append(line)
            patched_content = '\n'.join(new_lines)

            # Add debug output around spark-submit invocations for tracing
            debug_lines = [
                'echo "[DEBUG] spark script start"',
                'echo "[DEBUG] pwd=$(pwd)"',
                'echo "[DEBUG] LOG_FILE=${LOG_FILE:-/dev/stdout}"',
                'echo "[DEBUG] PYSPARK_PYTHON=${PYSPARK_PYTHON:-}"',
                'echo "[DEBUG] PYSPARK_DRIVER_PYTHON=${PYSPARK_DRIVER_PYTHON:-}"',
                'echo "[DEBUG] SPARK_HOME=${SPARK_HOME:-}"',
                'echo "[DEBUG] ls -la ."; ls -la .',
                'echo "[DEBUG] ls -la .."; ls -la ..',
                'echo "[DEBUG] ls -la ../test-data"; ls -la ../test-data 2>/dev/null || true',
                'echo "[DEBUG] which spark-submit"; which spark-submit || true',
                'echo "[DEBUG] spark-submit --version"; spark-submit --version 2>/dev/null || true'
            ]
            patched_lines = patched_content.splitlines()
            with_debug = []
            injected = False
            for line in patched_lines:
                if not injected and line.strip().startswith('cd '):
                    with_debug.append(line)
                    with_debug.extend(debug_lines)
                    injected = True
                    continue
                with_debug.append(line)
            if not injected:
                with_debug = debug_lines + with_debug
            patched_content = '\n'.join(with_debug)

            with open(spark_script, 'w') as f:
                f.write(patched_content)

            spark_script.chmod(0o755)
            print("  [OK] spark script patched for stdout and local paths")


        

    def fix_benchmark_specific_issues(self):
        """
        Fix Spark 3.3.0 issues.
        
        JDK 17 CLEANUP:
        - Removed all JDK 21/25 compatibility flags (SecurityManager, Add-Opens).
        - Removed all env var injections (SPARK_JAVA_OPTS, JDK_JAVA_OPTIONS).
        - Retained critical path fixes ($HOME, cd).
        - Retained data generation step (without extra flags).
        - ACTIVELY SANITIZES install.sh to remove legacy flags.
        """
        pts_dir = Path.home() / ".phoronix-test-suite"
        install_sh = pts_dir / "installed-tests" / "pts" / "spark-1.0.1" / "install.sh"
        if not install_sh.exists():
            return False
            
        try:
            with open(install_sh, 'r') as f:
                content = f.read()
            
            modified = False
            
            # --- Path Fixes & Data Generation (Critical for install.sh) ---
            
            # 1. Fix data paths ($HOME -> ../..)
            # The install.sh uses $HOME/pyspark-benchmark which fails in PTS environment.
            if '\\$HOME/pyspark-benchmark' in content:
                print("  [FIX] Patching install.sh paths (\\$HOME -> ../..)...")
                content = content.replace('\\$HOME/pyspark-benchmark', '../../pyspark-benchmark')
                content = content.replace('\\$HOME/test-data', '../../test-data')
                modified = True
            elif '$HOME/pyspark-benchmark' in content:
                print("  [FIX] Patching install.sh paths ($HOME -> ../..)...")
                content = content.replace('$HOME/pyspark-benchmark', '../../pyspark-benchmark')
                content = content.replace('$HOME/test-data', '../../test-data')
                modified = True
            
            # 2. Fix cd command (remove ~/)
            target_cd = "cd ~/spark-3.3.0-bin-hadoop3/bin"
            rep_cd = "cd spark-3.3.0-bin-hadoop3/bin"
            if target_cd in content:
                 content = content.replace(target_cd, rep_cd)
                 modified = True
            
            # 3. Fix spark-defaults path (Clean path fix only)
            target_conf = "> ~/spark-3.3.0-bin-hadoop3/conf/spark-defaults.conf"
            rep_conf = "> spark-3.3.0-bin-hadoop3/conf/spark-defaults.conf"
            if target_conf in content:
                  content = content.replace(target_conf, rep_conf)
                  modified = True

            # 4. Generate test data if missing
            # CLEAN VERSION: No --driver-java-options
            gen_cmd_clean = "./spark-3.3.0-bin-hadoop3/bin/spark-submit --driver-memory 4g pyspark-benchmark/generate-data.py test-data"

            if 'generate-data.py' not in content:
                 print("  [FIX] Injecting data generation step into install.sh...")
                 content += f"\n# Generate test data (added by pts_runner clean)\n{gen_cmd_clean}\n"
                 modified = True
            
            # 5. SANITIZATION: Remove Toxic Flags (Leftover from previous runs or injections)
            import re
            
            # A. Remove export SPARK_JAVA_OPTS / JDK_JAVA_OPTIONS
            if "export SPARK_JAVA_OPTS=" in content or "export JDK_JAVA_OPTIONS=" in content:
                  print("  [FIX] Sanitizing install.sh: Removing toxic env exports...")
                  content = re.sub(r"export SPARK_JAVA_OPTS=.*\n", "", content)
                  content = re.sub(r"export JDK_JAVA_OPTIONS=.*\n", "", content)
                  # Cleanup bare exports without newlines just in case
                  content = re.sub(r"export SPARK_JAVA_OPTS='.*'", "", content) 
                  modified = True
            
            # B. Remove --driver-java-options from 'spark' launcher script
            if "./spark-submit --driver-java-options" in content:
                  print("  [FIX] Sanitizing install.sh: Removing --driver-java-options from launcher...")
                  # Replace with clean version
                  # Pattern: ./spark-submit --driver-java-options '...' --name
                  # We simply remove the flag and its argument.
                  content = re.sub(r"--driver-java-options \'.*?\' ", "", content)
                  content = re.sub(r"--driver-java-options \".*?\" ", "", content)
                  modified = True

            # C. Clean up generate-data.py line if it has flags
            # Find any line with generate-data.py AND --driver-java-options
            lines = content.split('\n')
            new_lines = []
            for line in lines:
                if "generate-data.py" in line and "--driver-java-options" in line:
                    print("  [FIX] Sanitizing install.sh: Reverting data gen to clean command...")
                    new_lines.append(gen_cmd_clean)
                    modified = True
                else:
                    new_lines.append(line)
            content = '\n'.join(new_lines)

            # 5b. Patch pre.sh paths to use installed test directory
            pre_sh = pts_dir / "test-profiles" / "pts" / "spark-1.0.1" / "pre.sh"
            if pre_sh.exists():
                try:
                    pre_content = pre_sh.read_text()
                    pre_content = pre_content.replace(
                        "cd ~/spark-3.3.0-bin-hadoop3/bin",
                        "cd spark-3.3.0-bin-hadoop3/bin"
                    )
                    pre_content = pre_content.replace(
                        "$HOME/pyspark-benchmark",
                        "../../pyspark-benchmark"
                    )
                    pre_content = pre_content.replace(
                        "$HOME/test-data",
                        "../../test-data"
                    )
                    pre_sh.write_text(pre_content)
                    print("  [OK] pre.sh patched for local paths")
                except Exception as e:
                    print(f"  [WARN] Failed to patch pre.sh: {e}")

            # 6. Python 3.12+ compatibility (typing.io / typing.re / pipes removal)
            if self.py_version >= (3, 12):
                print("  [FIX] Python 3.12+ detected. Starting robust compatibility patches...")

                # Ensure all files are writable before patching
                subprocess.run(['chmod', '-R', '+w', str(install_sh.parent)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                # 6a. Shadowing Check: Remove any local 'typing.py' that shadows the stdlib
                # This is the primary cause of "'typing' is not a package"
                shadow_files = list(install_sh.parent.rglob("typing.py*"))
                if shadow_files:
                    print(f"  [FIX] Found {len(shadow_files)} shadowing 'typing' files. Removing them...")
                    for sf in shadow_files:
                        print(f"    [DEL] {sf.relative_to(install_sh.parent)}")
                        try:
                            if sf.is_file(): sf.unlink()
                            elif sf.is_dir(): shutil.rmtree(sf)
                        except Exception as e:
                            print(f"    [ERR] Failed to delete {sf.name}: {e}")

                # 6b. Extract PySpark zips and patch Spark launcher scripts
                # Critical: Spark's bin/pyspark and bin/spark-submit add zip files to PYTHONPATH
                # We must extract zips AND modify launcher scripts to use extracted directories
                found_spark_dir = False
                for spark_dir in install_sh.parent.glob("spark-3.3.0*"):
                    found_spark_dir = True
                    python_dir = spark_dir / "python"
                    lib_dir = python_dir / "lib"
                    bin_dir = spark_dir / "bin"
                    print(f"  [FIX] Processing Spark directory: {spark_dir.name}")

                    # Extract zip files
                    if lib_dir.exists():
                        for zip_file in lib_dir.glob("*.zip"):
                            print(f"    [ZIP] Extracting {zip_file.name} into {python_dir.name}...")
                            try:
                                res = subprocess.run(['unzip', '-o', str(zip_file), '-d', str(python_dir)], capture_output=True, text=True)
                                if res.returncode == 0:
                                    # Keep zip but rename to .zip.bak so scripts don't find it
                                    # Note: with_suffix('.zip.bak') would produce 'pyspark.bak', not 'pyspark.zip.bak'
                                    bak_path = zip_file.with_name(zip_file.name + '.bak')
                                    zip_file.rename(bak_path)
                                    print(f"    [OK] Extracted and renamed to {bak_path.name}")
                                else:
                                    print(f"    [ERR] Unzip failed: {res.stderr}")
                            except Exception as e:
                                print(f"    [WARN] Failed to extract {zip_file.name}: {e}")

                    # 6b-1.5. Post-extraction: Remove typing.py shadow files from extracted pyspark
                    # CRITICAL: ZIP extraction may create typing.py files that shadow stdlib
                    post_shadow_files = list(python_dir.rglob("typing.py")) + list(python_dir.rglob("typing.pyc"))
                    if post_shadow_files:
                        print(f"    [FIX] Found {len(post_shadow_files)} typing.py shadow files after ZIP extraction. Removing...")
                        for sf in post_shadow_files:
                            try:
                                sf.unlink()
                                print(f"      [DEL] {sf.relative_to(python_dir)}")
                            except Exception as e:
                                print(f"      [ERR] Failed to delete {sf.name}: {e}")

                    # 6b-2. Patch Spark launcher scripts (pyspark, spark-submit, load-spark-env.sh)
                    # These scripts add zip files to PYTHONPATH - we need to modify them
                    launcher_scripts = ['pyspark', 'spark-submit', 'load-spark-env.sh']
                    for script_name in launcher_scripts:
                        script_path = bin_dir / script_name
                        if script_path.exists():
                            try:
                                with open(script_path, 'r') as f:
                                    script_content = f.read()

                                # Replace zip file references with directory references
                                # Pattern: ${SPARK_HOME}/python/lib/pyspark.zip -> ${SPARK_HOME}/python
                                # Pattern: ${SPARK_HOME}/python/lib/py4j-*.zip -> ${SPARK_HOME}/python
                                original_content = script_content

                                # Remove zip file additions to PYTHONPATH
                                script_content = re.sub(
                                    r'\$\{SPARK_HOME\}/python/lib/pyspark\.zip[:\"]?',
                                    '${SPARK_HOME}/python:',
                                    script_content
                                )
                                script_content = re.sub(
                                    r'\$\{SPARK_HOME\}/python/lib/py4j[^:\"]*\.zip[:\"]?',
                                    '',
                                    script_content
                                )
                                # Also handle $SPARK_HOME without braces
                                script_content = re.sub(
                                    r'\$SPARK_HOME/python/lib/pyspark\.zip[:\"]?',
                                    '$SPARK_HOME/python:',
                                    script_content
                                )
                                script_content = re.sub(
                                    r'\$SPARK_HOME/python/lib/py4j[^:\"]*\.zip[:\"]?',
                                    '',
                                    script_content
                                )

                                if script_content != original_content:
                                    with open(script_path, 'w') as f:
                                        f.write(script_content)
                                    print(f"    [OK] Patched {script_name} to use extracted directories")
                            except Exception as e:
                                print(f"    [WARN] Failed to patch {script_name}: {e}")

                    # 6b-3. Create a PYTHONPATH setup script for runtime use
                    pythonpath_setup = python_dir / "setup_pythonpath.sh"
                    try:
                        with open(pythonpath_setup, 'w') as f:
                            f.write(f'''#!/bin/bash
# Auto-generated PYTHONPATH setup for Python 3.13+ compatibility
export PYTHONPATH="{python_dir}:${{PYTHONPATH}}"
''')
                        pythonpath_setup.chmod(0o755)
                        print(f"    [OK] Created {pythonpath_setup.name}")
                        # Store for later use in run_benchmark
                        self.spark_python_dir = str(python_dir)
                    except Exception as e:
                        print(f"    [WARN] Failed to create PYTHONPATH setup: {e}")

                if not found_spark_dir:
                    print(f"  [WARN] No 'spark-3.3.0*' directory found in {install_sh.parent}")

                # 6c. Patch Python source files with accurate import replacements
                print("  [FIX] Running import replacements for typing.io, typing.re, and pipes...")

                # More accurate sed patterns for Python imports
                # Note: Using word boundaries and flexible whitespace to handle indented code
                patch_cmds = [
                    # Fix typing.io imports - Python 3.13 removed typing.io submodule
                    # 'from typing.io import X' -> 'from typing import X'
                    f"find -L {install_sh.parent} -name '*.py' -exec sed -i 's/from typing\\.io import/from typing import/g' {{}} +",
                    # 'import typing.io' -> 'import typing' (handles indentation)
                    f"find -L {install_sh.parent} -name '*.py' -exec sed -i 's/\\bimport typing\\.io\\b/import typing/g' {{}} +",
                    # 'typing.io.X' -> 'typing.X'
                    f"find -L {install_sh.parent} -name '*.py' -exec sed -i 's/typing\\.io\\./typing./g' {{}} +",

                    # Fix typing.re imports - Python 3.13 removed typing.re submodule
                    f"find -L {install_sh.parent} -name '*.py' -exec sed -i 's/from typing\\.re import/from typing import/g' {{}} +",
                    f"find -L {install_sh.parent} -name '*.py' -exec sed -i 's/\\bimport typing\\.re\\b/import typing/g' {{}} +",
                    f"find -L {install_sh.parent} -name '*.py' -exec sed -i 's/typing\\.re\\./typing./g' {{}} +",

                    # Fix pipes module (removed in Python 3.13)
                    # 'import pipes' -> 'import shlex' (handles indentation with word boundary)
                    f"find -L {install_sh.parent} -name '*.py' -exec sed -i 's/\\bimport pipes\\b/import shlex/g' {{}} +",
                    # 'from pipes import quote' -> 'from shlex import quote'
                    f"find -L {install_sh.parent} -name '*.py' -exec sed -i 's/from pipes import/from shlex import/g' {{}} +",
                    # 'pipes.quote' -> 'shlex.quote'
                    f"find -L {install_sh.parent} -name '*.py' -exec sed -i 's/\\bpipes\\.quote/shlex.quote/g' {{}} +",
                    # 'pipes.Template' and other pipes usages -> comment out or replace
                    f"find -L {install_sh.parent} -name '*.py' -exec sed -i 's/\\bpipes\\./shlex./g' {{}} +",

                    # Delete all pycache to force Python to re-read the patched source files
                    f"find -L {install_sh.parent} -name '__pycache__' -type d -exec rm -rf {{}} +",
                    f"find -L {install_sh.parent} -name '*.pyc' -type f -delete"
                ]
                for cmd in patch_cmds:
                    subprocess.run(['bash', '-c', f"LC_ALL=C {cmd}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                # 6d. Verification step with detailed output
                print("  [FIX] Verifying patches...")
                check = subprocess.run(
                    ['bash', '-c', f"grep -r 'typing\\.io' {install_sh.parent} --include='*.py' 2>/dev/null || true"],
                    capture_output=True, text=True
                )
                if not check.stdout.strip():
                    print("  [OK] No 'typing.io' found in Python files. Patch verified.")
                else:
                    print("  [WARN] 'typing.io' still exists in the following files:")
                    for line in check.stdout.strip().splitlines()[:5]:
                        print(f"    [REMAIN] {line}")

                # Final check for any remaining typing.py shadowing files
                final_shadow = list(install_sh.parent.rglob("typing.py"))
                if final_shadow:
                    print("  [CRITICAL] Shadowing typing.py files still exist:")
                    for sf in final_shadow:
                        print(f"    [SHADOW] {sf}")
                else:
                    print("  [OK] No shadowing typing.py files found.")

                sys.stdout.flush()

            if modified:
                with open(install_sh, 'w') as f:
                    f.write(content)
                return True
                
        except Exception as e:
            print(f"  [WARN] Failed to patch install.sh: {e}")
        
        return False

        
    def generate_summary(self):
        """Generate summary.log and summary.json from all thread results."""
        print(f"\n{'='*80}")
        print(">>> Generating summary")
        print(f"{'='*80}")

        summary_log = self.results_dir / "summary.log"

        # Collect results from all JSON files
        all_results = []
        for num_threads in self.thread_list:
            json_file = self.results_dir / f"{num_threads}-thread.json"
            if json_file.exists():
                with open(json_file, 'r') as f:
                    data = json.load(f)
                    # Extract benchmark result
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
            f.write("Spark Benchmark Summary\n")
            f.write(f"Machine: {self.machine_name}\n")
            f.write(f"Test Category: {self.test_category}\n")
            f.write("="*80 + "\n\n")

            for result in all_results:
                f.write(f"Threads: {result['threads']}\n")
                f.write(f"  Test: {result['test_name']}\n")
                f.write(f"  Description: {result['description']}\n")
                val_str = f"{result['value']:.2f}" if result['value'] is not None else "FAILED"
                f.write(f"  Average: {val_str} {result['unit']}\n")
                
                raw_values = result.get('raw_values')
                if raw_values:
                    f.write(f"  Raw values: {', '.join([f'{v:.2f}' for v in raw_values])}\n")
                else:
                    f.write("  Raw values: N/A\n")
                f.write("\n")

            f.write("="*80 + "\n")
            f.write("Summary Table\n")
            f.write("="*80 + "\n")
            f.write(f"{'Threads':<10} {'Average':<15} {'Unit':<20}\n")
            f.write("-"*80 + "\n")
            for result in all_results:
                val_str = f"{result['value']:.2f}" if result['value'] is not None else "N/A"
                f.write(f"{result['threads']:<10} {val_str:<15} {result['unit']:<20}\n")

        print(f"[OK] Summary log saved: {summary_log}")

    def parse_perf_stats_and_freq(self, perf_stats_file, freq_start_file, freq_end_file, cpu_list):
        """Parse perf stat output and CPU frequency files."""
        print("\n>>> Parsing perf stats and frequency data")

        cpu_ids = [int(c.strip()) for c in cpu_list.split(',')]
        per_cpu_metrics = {}
        for cpu_id in cpu_ids:
            per_cpu_metrics[cpu_id] = {
                'cycles': 0,
                'instructions': 0,
                'cpu_clock': 0,
                'task_clock': 0,
                'context_switches': 0,
                'cpu_migrations': 0
            }

        # Parse perf stat output
        try:
            with open(perf_stats_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue

                    # Match CPU-specific lines
                    # Format: "CPU<n>   <value>   <event>"
                    # Example: "CPU0                123,456      cycles"
                    # Example: "CPU0           123.45 msec      task-clock"
                    match = re.match(r'CPU(\d+)\s+([\d,.<>a-zA-Z\s]+)\s+([a-zA-Z0-9\-_]+)', line)
                    if match:
                        cpu_num = int(match.group(1))
                        value_str = match.group(2).strip()
                        event = match.group(3)

                        # Only process CPUs in our cpu_list
                        if cpu_num not in cpu_ids:
                            continue

                        if '<not supported>' in value_str:
                            continue

                        try:
                            # Remove units like "msec" if present (e.g. "123.45 msec" -> "123.45")
                            value_clean = value_str.split()[0]
                            value = float(value_clean.replace(',', ''))
                        except ValueError:
                            print(f"  [WARN] Failed to parse value '{value_str}' for CPU{cpu_num} {event}")
                            continue

                        if event == 'cycles':
                            per_cpu_metrics[cpu_num]['cycles'] = value
                        elif event == 'instructions':
                            per_cpu_metrics[cpu_num]['instructions'] = value
                        elif event == 'cpu-clock':
                            per_cpu_metrics[cpu_num]['cpu_clock'] = value
                        elif event == 'task-clock':
                            per_cpu_metrics[cpu_num]['task_clock'] = value
                        elif event == 'context-switches':
                            per_cpu_metrics[cpu_num]['context_switches'] = value
                        elif event == 'cpu-migrations':
                            per_cpu_metrics[cpu_num]['cpu_migrations'] = value

        except Exception as e:
            print(f"  [ERROR] Failed to parse perf stat file: {e}")
            raise

        # Parse frequency files
        freq_start = {}
        freq_end = {}

        try:
            with open(freq_start_file, 'r') as f:
                lines = f.read().strip().split('\n')
                for i, line in enumerate(lines):
                    if line.strip():
                        freq_start[i] = float(line.strip())

            with open(freq_end_file, 'r') as f:
                lines = f.read().strip().split('\n')
                for i, line in enumerate(lines):
                    if line.strip():
                        freq_end[i] = float(line.strip())

        except Exception as e:
            print(f"  [ERROR] Failed to parse frequency files: {e}")
            raise

        # Calculate metrics
        perf_summary = {
            'avg_frequency_ghz': {},
            'start_frequency_ghz': {},
            'end_frequency_ghz': {},
            'ipc': {},
            'total_cycles': {},
            'total_instructions': {},
            'cpu_utilization_percent': 0.0,
            'elapsed_time_sec': 0.0
        }

        total_task_clock = 0.0
        max_task_clock = 0.0

        for cpu_id in cpu_ids:
            metrics = per_cpu_metrics[cpu_id]

            if metrics['cpu_clock'] > 0:
                avg_freq = metrics['cycles'] / (metrics['cpu_clock'] / 1000.0) / 1e9
                perf_summary['avg_frequency_ghz'][str(cpu_id)] = round(avg_freq, 3)
            else:
                perf_summary['avg_frequency_ghz'][str(cpu_id)] = 0.0

            if cpu_id in freq_start:
                start_freq = freq_start[cpu_id] / 1_000_000.0
                perf_summary['start_frequency_ghz'][str(cpu_id)] = round(start_freq, 3)
            else:
                perf_summary['start_frequency_ghz'][str(cpu_id)] = 0.0

            if cpu_id in freq_end:
                end_freq = freq_end[cpu_id] / 1_000_000.0
                perf_summary['end_frequency_ghz'][str(cpu_id)] = round(end_freq, 3)
            else:
                perf_summary['end_frequency_ghz'][str(cpu_id)] = 0.0

            if metrics['cycles'] > 0:
                ipc = metrics['instructions'] / metrics['cycles']
                perf_summary['ipc'][str(cpu_id)] = round(ipc, 2)
            else:
                perf_summary['ipc'][str(cpu_id)] = 0.0

            perf_summary['total_cycles'][str(cpu_id)] = int(metrics['cycles'])
            perf_summary['total_instructions'][str(cpu_id)] = int(metrics['instructions'])

            total_task_clock += metrics['task_clock']
            max_task_clock = max(max_task_clock, metrics['task_clock'])

        if max_task_clock > 0:
            perf_summary['elapsed_time_sec'] = round(max_task_clock / 1000.0, 2)
            utilization = (total_task_clock / max_task_clock / len(cpu_ids)) * 100.0
            perf_summary['cpu_utilization_percent'] = round(utilization, 1)

        print("  [OK] Performance metrics calculated")
        return perf_summary

    def run_benchmark(self, num_threads):
        """Run benchmark with specified thread count."""
        print(f"\n{'='*80}")
        print(f">>> Running benchmark with {num_threads} thread(s)")
        print(f"{'='*80}")

        # Create output directory
        self.results_dir.mkdir(parents=True, exist_ok=True)
        log_file = self.results_dir / f"{num_threads}-thread.log"
        stdout_log = self.results_dir / "stdout.log"

        perf_stats_file = self.results_dir / f"{num_threads}-thread_perf_stats.txt"
        freq_start_file = self.results_dir / f"{num_threads}-thread_freq_start.txt"
        freq_end_file = self.results_dir / f"{num_threads}-thread_freq_end.txt"
        perf_summary_file = self.results_dir / f"{num_threads}-thread_perf_summary.json"

        # Build PTS command
        # Environment variables to suppress all prompts
        # BATCH_MODE, SKIP_ALL_PROMPTS: additional safeguards
        # TEST_RESULTS_NAME, TEST_RESULTS_IDENTIFIER: auto-generate result names
        # DISPLAY_COMPACT_RESULTS: suppress "view text results" prompt
        # Note: PTS_USER_PATH_OVERRIDE removed - use default ~/.phoronix-test-suite/ with batch-setup config
        # Java 25 compatibility flags for Spark 3.3
        # SPARK_JAVA_OPTS is legacy. JDK_JAVA_OPTIONS is robust for Java 9+.
        # We must provide these to the runtime environment so spark-submit picks them up.
        java_opts = "--add-opens=java.base/java.lang=ALL-UNNAMED --add-opens=java.base/java.lang.invoke=ALL-UNNAMED --add-opens=java.base/java.lang.reflect=ALL-UNNAMED --add-opens=java.base/java.io=ALL-UNNAMED --add-opens=java.base/java.net=ALL-UNNAMED --add-opens=java.base/java.nio=ALL-UNNAMED --add-opens=java.base/java.util=ALL-UNNAMED --add-opens=java.base/java.util.concurrent=ALL-UNNAMED --add-opens=java.base/java.util.concurrent.atomic=ALL-UNNAMED --add-opens=java.base/sun.nio.ch=ALL-UNNAMED --add-opens=java.base/sun.nio.cs=ALL-UNNAMED --add-opens=java.base/sun.security.action=ALL-UNNAMED --add-opens=java.base/sun.util.calendar=ALL-UNNAMED --add-opens=java.security.jgss/sun.security.krb5=ALL-UNNAMED --add-opens=java.base/javax.security.auth=ALL-UNNAMED"
        
        # We set BOTH SPARK_JAVA_OPTS (legacy) and JDK_JAVA_OPTIONS (robust)
        spark_opts = f'SPARK_JAVA_OPTS="{java_opts}" JDK_JAVA_OPTIONS="{java_opts}" '

        quick_env = 'FORCE_TIMES_TO_RUN=1 ' if self.quick_mode else ''

        # Python 3.12+ compatibility: Set PYTHONPATH to use patched pyspark directory
        # This ensures Python finds the patched pyspark modules instead of broken zip files
        pythonpath_env = ''
        if self.spark_python_dir:
            pythonpath_env = f'PYTHONPATH="{self.spark_python_dir}:$PYTHONPATH" '
            print(f"[INFO] Python 3.12+ mode: PYTHONPATH set to {self.spark_python_dir}")

        python_exec_env = ''
        if self.spark_python_exec:
            python_exec_env = f'PYSPARK_PYTHON={self.spark_python_exec} PYSPARK_DRIVER_PYTHON={self.spark_python_exec} '
            print(f"[INFO] Spark Python set to {self.spark_python_exec}")

        # Remove existing PTS result to avoid interactive prompts
        # PTS sanitizes identifiers (e.g. 1.0.2 -> 102), so we try to remove both forms
        sanitized_benchmark = self.benchmark.replace('.', '')
        remove_cmds = [
            f'phoronix-test-suite remove-result {self.benchmark}-{num_threads}threads',
            f'phoronix-test-suite remove-result {sanitized_benchmark}-{num_threads}threads'
        ]
        for cmd in remove_cmds:
            subprocess.run(['bash', '-c', cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        batch_env = f'{quick_env}{python_exec_env}{pythonpath_env}{spark_opts}BATCH_MODE=1 SKIP_ALL_PROMPTS=1 DISPLAY_COMPACT_RESULTS=1 TEST_RESULTS_NAME={self.benchmark}-{num_threads}threads TEST_RESULTS_IDENTIFIER={self.benchmark}-{num_threads}threads TEST_RESULTS_DESCRIPTION={self.benchmark}-{num_threads}threads'

        if num_threads >= self.vcpu_count:
            cpu_list = ','.join([str(i) for i in range(self.vcpu_count)])
            pts_base_cmd = f'phoronix-test-suite batch-run {self.benchmark_full}'
            cpu_info = f"Using all {num_threads} vCPUs (no taskset)"
        else:
            cpu_list = self.get_cpu_affinity_list(num_threads)
            pts_base_cmd = f'taskset -c {cpu_list} phoronix-test-suite batch-run {self.benchmark_full}'
            cpu_info = f"CPU affinity (taskset): {cpu_list}"

        # CRITICAL: Environment variables MUST come BEFORE perf stat
        if self.perf_events:
            if self.perf_paranoid <= 0:
                # Full monitoring mode: per-CPU stats + hardware counters
                pts_cmd = f'NUM_CPU_CORES={num_threads} {batch_env} perf stat -e {self.perf_events} -A -a -o {perf_stats_file} {pts_base_cmd}'
                perf_mode = "Full (per-CPU + HW counters)"
            else:
                # Limited mode: aggregated events only (no -A -a)
                pts_cmd = f'NUM_CPU_CORES={num_threads} {batch_env} perf stat -e {self.perf_events} -o {perf_stats_file} {pts_base_cmd}'
                perf_mode = "Limited (aggregated events only)"
        else:
            # No perf monitoring
            pts_cmd = f'NUM_CPU_CORES={num_threads} {batch_env} {pts_base_cmd}'
            perf_mode = "Disabled (perf unavailable)"
        
        print(f"[INFO] Perf monitoring mode: {perf_mode}")

        print(f"[INFO] {cpu_info}")
        print(f"\n{'>'*80}")
        print("[PTS BENCHMARK COMMAND]")
        print(f"  {pts_cmd}")
        print(f"{'<'*80}\n")

        # Record CPU frequency before benchmark
        # Uses cross-platform method (works on x86_64, ARM64, and cloud VMs)
        print("[INFO] Recording CPU frequency before benchmark...")
        if self.record_cpu_frequency(freq_start_file):
            print("  [OK] Start frequency recorded")
        else:
            print("  [WARN] CPU frequency not available (common on ARM64/cloud VMs)")

        # Execute PTS command
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
                log_f.flush()
                stdout_f.flush()

            process.wait()
            returncode = process.returncode

        pts_test_failed, pts_failure_reason = detect_pts_failure_from_log(log_file)

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

            try:
                perf_summary = self.parse_perf_stats_and_freq(
                    perf_stats_file,
                    freq_start_file,
                    freq_end_file,
                    cpu_list
                )

                with open(perf_summary_file, 'w') as f:
                    json.dump(perf_summary, f, indent=2)
                print(f"     Perf summary: {perf_summary_file}")

            except Exception as e:
                print(f"  [ERROR] Failed to parse perf stats: {e}")

        else:
            print(f"\n[ERROR] Benchmark failed with return code {returncode}")
            err_file = self.results_dir / f"{num_threads}-thread.err"
            with open(err_file, 'w') as f:
                f.write(f"Benchmark failed with return code {returncode}\n")
                f.write(f"See {log_file} for details.\n")
            print(f"     Error log: {err_file}")
            return False

        return True

    def export_results(self):
        """Export benchmark results to CSV and JSON formats."""
        print(f"\n{'='*80}")
        print(">>> Exporting benchmark results")
        print(f"{'='*80}")

        pts_results_dir = Path.home() / ".phoronix-test-suite" / "test-results"

        for num_threads in self.thread_list:
            result_name = f"{self.benchmark}-{num_threads}threads"

            # CRITICAL: PTS removes dots from directory names
            result_dir_name = result_name.replace('.', '')
            result_dir = pts_results_dir / result_dir_name
            if not result_dir.exists():
                print(f"[WARN] Result not found for {num_threads} threads")
                continue

            print(f"\n[INFO] Exporting results for {num_threads} thread(s)...")

            # Export to CSV
            csv_output = self.results_dir / f"{num_threads}-thread.csv"
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

            # Export to JSON
            json_output = self.results_dir / f"{num_threads}-thread.json"
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

        print("\n[OK] Export completed")



    def run(self):
        """Main execution flow."""
        print(f"{'='*80}")
        print("Spark 1.0.1 Benchmark Runner")
        print(f"{'='*80}")
        print(f"[INFO] Machine: {self.machine_name}")
        print(f"[INFO] vCPU count: {self.vcpu_count}")
        print(f"[INFO] Threads to test: {self.thread_list}")
        print()

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

        stdout_log = self.results_dir / "stdout.log"
        with open(stdout_log, 'a') as stdout_f:
            stdout_f.write(f"{'='*80}\n")
            stdout_f.write("[RUNNER STARTUP]\n")
            stdout_f.write(f"Python: {sys.version.split()[0]}\n")
            stdout_f.write(f"Python exec override: {self.spark_python_exec or '-'}\n")
            stdout_f.write(f"PYTHONPATH override: {self.spark_python_dir or '-'}\n")
            stdout_f.write(f"Machine: {self.machine_name}\n")
            stdout_f.write(f"OS: {self.os_name}\n")
            stdout_f.write(f"vCPU: {self.vcpu_count}\n")
            stdout_f.write(f"Threads: {self.thread_list}\n")
            stdout_f.write(f"Perf events: {self.perf_events or '-'}\n")
            stdout_f.write(f"Perf paranoid: {self.perf_paranoid}\n")
            stdout_f.write(f"{'='*80}\n\n")

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
        self.fix_benchmark_specific_issues()

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
        print(f"{'='*80}")

        return len(failed) == 0


def main():
    parser = argparse.ArgumentParser(
        description="Spark 1.0.1 Benchmark Runner",
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

    runner = SparkRunner(threads_arg=threads, quick_mode=args.quick)
    success = runner.run()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
