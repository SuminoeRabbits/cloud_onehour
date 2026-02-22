#!/usr/bin/env python3
"""
PTS Runner for x265-1.5.0

System Dependencies (from phoronix-test-suite info):
- Software Dependencies:
  * C/C++ Compiler Toolchain
  * 7-Zip / p7zip
  * CMake
  * Yasm Assembler
- Estimated Install Time: 238 Seconds
- Environment Size: 3400 MB
- Test Type: Processor
- Supported Platforms: Linux, MacOSX, BSD

Test Characteristics:
- Multi-threaded: Yes (video encoding is highly parallel)
- Honors CFLAGS/CXXFLAGS: Yes
- Notable Instructions: MMX, SSE, SSE2, SVE2 support for ARM architectures
- THFix_in_compile: false - Thread count NOT fixed at compile time
- THChange_at_runtime: false - x265 auto-detects CPU cores (no explicit thread control in PTS)

Note: x265 supports --pools and --threads options but PTS test doesn't use them.
This runner uses taskset to limit CPU availability for thread scaling tests.
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
            benchmark_name: Full benchmark name (e.g., "pts/x265-1.5.0")
            threshold_mb: Size threshold in MB to trigger aria2c (default: 256MB)
        """
        if not self.aria2_available:
            print("  [INFO] aria2c not found, skipping pre-seed (will rely on PTS default)")
            return False

        # Locate downloads.xml
        # ~/.phoronix-test-suite/test-profiles/<benchmark_name>/downloads.xml
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
        print(f"  [INFO] Parsing {profile_path}...")
        try:
            import xml.etree.ElementTree as ET
            tree = ET.parse(profile_path)
            root = tree.getroot()
            
            # Find all Package elements
            # Structure: <Downloads><Package><URL>...</URL><FileName>...</FileName><FileSize>...</FileSize></Package>...</Downloads>
            downloads_node = root.find('Downloads')
            if downloads_node is None:
                print("  [WARN] No <Downloads> section found in XML")
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
                
                # If size not in XML, try to get it from network (fallback)
                if size_bytes <= 0:
                    print(f"  [CHECK] Size not in XML, checking remote for {filename}...")
                    size_bytes = self.get_remote_file_size(url)
                    
                # Check threshold
                if size_bytes > 0:
                    size_mb = size_bytes / (1024 * 1024)
                    if size_mb < threshold_mb:
                        print(f"  [SKIP] {filename} is small ({size_mb:.1f} MB < {threshold_mb} MB)")
                        continue
                    print(f"  [INFO] {filename} is large ({size_mb:.1f} MB), accelerating with aria2c...")
                    self.ensure_file(url, filename)
                else:
                     print(f"  [WARN] Could not determine size for {filename}, skipping auto-download")

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
            # -s: Silent, -I: Header only, -L: Follow redirects
            cmd = ['curl', '-s', '-I', '-L', url]
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                print(f"  [WARN] Failed to get headers for {url}")
                return -1
                
            # Parse Content-Length
            # Look for "content-length: 12345" (case insensitive)
            for line in result.stdout.splitlines():
                if line.lower().startswith('content-length:'):
                    try:
                        size_str = line.split(':')[1].strip()
                        return int(size_str)
                    except ValueError:
                        pass
        except Exception as e:
            print(f"  [WARN] Error checking size: {e}")
            
        return -1

    def ensure_file(self, url, filename):
        """
        Directly download file using aria2c (assumes size check passed).
        """
        target_path = self.cache_dir / filename
        
        # Check if file exists in cache
        if target_path.exists():
            print(f"  [CACHE] File found: {filename}")
            return True

        # Need to download
        print(f"  [ARIA2] Downloading {filename} with 16 connections...")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # aria2c command
        cmd = [
            "aria2c", "-x", "16", "-s", "16", 
            "-d", str(self.cache_dir), 
            "-o", filename,
            url
        ]
        
        try:
            subprocess.run(cmd, check=True)
            print(f"  [aria2c] Download completed: {filename}")
            return True
        except subprocess.CalledProcessError:
            print("  [WARN] aria2c download failed, falling back to PTS default")
            # Clean up partial download
            if target_path.exists():
                target_path.unlink()
            return False



class X265Runner:
    def __init__(self, threads_arg=None, quick_mode=False):
        """
        Initialize x265 runner.

        Args:
            threads_arg: Thread count argument (None for scaling mode, int for fixed mode)
            quick_mode: If True, run tests once (FORCE_TIMES_TO_RUN=1) for development
        """
        self.benchmark = "x265-1.5.0"
        self.benchmark_full = f"pts/{self.benchmark}"
        self.test_category = "Multimedia"
        # Replace spaces with underscores in test_category for directory name
        self.test_category_dir = self.test_category.replace(" ", "_")

        # System info
        self.vcpu_count = os.cpu_count() or 1
        self.machine_name = os.environ.get('MACHINE_NAME', os.uname().nodename)
        self.os_name = self.get_os_name()

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

    def ensure_7z_available(self):
        """Ensure 7z is available for extracting input Y4M files.
        
        7z (p7zip) should be pre-installed by scripts/setup_init.sh.
        This method only verifies its presence.
        """
        if shutil.which("7z"):
            return True

        print("  [ERROR] 7z command not found. Please run scripts/setup_init.sh first.")
        return False

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

    def dump_error_diagnostics(self, num_threads, log_file):
        """
        Dump diagnostic information when benchmark fails.

        Collects:
        1. PTS test-logs from ~/.phoronix-test-suite/test-results/
        2. x265 specific logs and error output
        3. Recent system dmesg output
        4. Installed test directory contents

        Args:
            num_threads: Number of threads used in the failed test
            log_file: Path to the benchmark log file
        """
        print(f"\n{'='*80}")
        print(">>> Dumping error diagnostics")
        print(f"{'='*80}")

        diag_file = self.results_dir / f"{num_threads}-thread_error_diag.txt"
        pts_home = Path.home() / ".phoronix-test-suite"

        with open(diag_file, 'w') as f:
            f.write("="*80 + "\n")
            f.write(f"ERROR DIAGNOSTICS - {self.benchmark} ({num_threads} threads)\n")
            f.write(f"Timestamp: {subprocess.run(['date'], capture_output=True, text=True).stdout.strip()}\n")
            f.write("="*80 + "\n\n")

            # 1. PTS test-logs
            f.write("-"*80 + "\n")
            f.write("[1] PTS Test Logs\n")
            f.write("-"*80 + "\n")

            test_results_dir = pts_home / "test-results"
            if test_results_dir.exists():
                # Find recent test result directories
                for result_dir in sorted(test_results_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True)[:3]:
                    test_logs_dir = result_dir / "test-logs"
                    if test_logs_dir.exists():
                        f.write(f"\n[Result: {result_dir.name}]\n")
                        for log in test_logs_dir.glob("**/*"):
                            if log.is_file():
                                f.write(f"\n--- {log.relative_to(test_logs_dir)} ---\n")
                                try:
                                    content = log.read_text(errors='ignore')
                                    # Limit to last 100 lines per log
                                    lines = content.split('\n')
                                    if len(lines) > 100:
                                        f.write("[...truncated, showing last 100 lines...]\n")
                                        f.write('\n'.join(lines[-100:]))
                                    else:
                                        f.write(content)
                                except Exception as e:
                                    f.write(f"[Error reading file: {e}]\n")
            else:
                f.write("No test-results directory found\n")

            # 2. Installed test logs
            f.write("\n\n" + "-"*80 + "\n")
            f.write("[2] Installed Test Directory\n")
            f.write("-"*80 + "\n")

            installed_dir = pts_home / "installed-tests" / "pts" / self.benchmark
            if installed_dir.exists():
                # List directory contents
                result = subprocess.run(
                    ['ls', '-la', str(installed_dir)],
                    capture_output=True, text=True
                )
                f.write(f"\nDirectory listing: {installed_dir}\n")
                f.write(result.stdout)

                # Check for any log files in installed test
                for log in installed_dir.glob("**/*.log"):
                    f.write(f"\n--- {log.relative_to(installed_dir)} ---\n")
                    try:
                        content = log.read_text(errors='ignore')
                        lines = content.split('\n')
                        if len(lines) > 50:
                            f.write("[...truncated, showing last 50 lines...]\n")
                            f.write('\n'.join(lines[-50:]))
                        else:
                            f.write(content)
                    except Exception as e:
                        f.write(f"[Error reading file: {e}]\n")

                # x265 specific: check for build/compile errors
                for err_file in installed_dir.glob("**/*.err"):
                    f.write(f"\n--- {err_file.relative_to(installed_dir)} ---\n")
                    try:
                        content = err_file.read_text(errors='ignore')
                        f.write(content[:5000])  # Limit size
                    except Exception as e:
                        f.write(f"[Error reading file: {e}]\n")
            else:
                f.write(f"Installed test directory not found: {installed_dir}\n")

            # 3. Try to run x265 manually to capture error
            f.write("\n\n" + "-"*80 + "\n")
            f.write("[3] x265 Binary Test\n")
            f.write("-"*80 + "\n")

            x265_bin = installed_dir / "x265"
            if x265_bin.exists():
                f.write("\n[x265 --version]\n")
                result = subprocess.run(
                    [str(x265_bin), '--version'],
                    capture_output=True, text=True
                )
                f.write(f"stdout: {result.stdout}\n")
                f.write(f"stderr: {result.stderr}\n")
                f.write(f"returncode: {result.returncode}\n")
            else:
                f.write(f"x265 binary not found at {x265_bin}\n")

            # 4. dmesg (last 30 lines)
            f.write("\n\n" + "-"*80 + "\n")
            f.write("[4] Recent dmesg Output\n")
            f.write("-"*80 + "\n")

            result = subprocess.run(
                ['dmesg', '--time-format=reltime'],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                lines = result.stdout.split('\n')
                f.write('\n'.join(lines[-30:]))
            else:
                # Try without --time-format for older systems
                result = subprocess.run(['dmesg'], capture_output=True, text=True)
                lines = result.stdout.split('\n')
                f.write('\n'.join(lines[-30:]))

            # 5. Memory and disk status
            f.write("\n\n" + "-"*80 + "\n")
            f.write("[5] System Resources\n")
            f.write("-"*80 + "\n")

            f.write("\n[free -h]\n")
            result = subprocess.run(['free', '-h'], capture_output=True, text=True)
            f.write(result.stdout)

            f.write("\n[df -h /]\n")
            result = subprocess.run(['df', '-h', '/'], capture_output=True, text=True)
            f.write(result.stdout)

            # 6. Check for missing libraries
            f.write("\n\n" + "-"*80 + "\n")
            f.write("[6] Library Dependencies\n")
            f.write("-"*80 + "\n")

            if x265_bin.exists():
                f.write(f"\n[ldd {x265_bin}]\n")
                result = subprocess.run(['ldd', str(x265_bin)], capture_output=True, text=True)
                f.write(result.stdout)
                if 'not found' in result.stdout:
                    f.write("\n*** WARNING: Missing libraries detected! ***\n")

            f.write("\n" + "="*80 + "\n")
            f.write("END OF DIAGNOSTICS\n")
            f.write("="*80 + "\n")

        print(f"  [DIAG] Error diagnostics saved: {diag_file}")

        # Also print key info to stdout
        print(f"  [INFO] Check {diag_file} for detailed diagnostics")
        return diag_file

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

    def patch_install_script(self):
        """
        Patch the install.sh script to fix x265 build issues on Ubuntu 24.04 with GCC-14.

        Problem: x265 4.1 uses CMake which doesn't pick up CFLAGS/CXXFLAGS from environment.
        Also, -march=native can generate instructions that x265's assembly code
        doesn't handle properly on some CPUs.

        Solution:
        1. Pass CMAKE_C_COMPILER and CMAKE_CXX_COMPILER explicitly
        2. Use -march=native for target-appropriate optimizations
        3. Add -Wno-error flags to suppress GCC-14 warnings-as-errors
        """
        install_sh_path = Path.home() / '.phoronix-test-suite' / 'test-profiles' / 'pts' / self.benchmark / 'install.sh'

        if not install_sh_path.exists():
            print(f"  [WARN] install.sh not found at {install_sh_path}")
            return False

        print("  [INFO] Patching install.sh for GCC-14 and Ubuntu 24.04 compatibility...")

        try:
            with open(install_sh_path, 'r') as f:
                content = f.read()

            patched = False

            # Patch: Replace cmake command with proper flags for GCC-14
            # Original: cmake -DCMAKE_BUILD_TYPE=Release ../source
            # New: cmake with explicit compiler and flags
            old_cmake = 'cmake -DCMAKE_BUILD_TYPE=Release ../source'
            arch = os.uname().machine
            if arch in ("x86_64", "amd64"):
                march_flags = "-O3 -march=x86-64-v3 -mtune=generic -Wno-error"
            else:
                march_flags = "-O3 -march=native -mtune=native -Wno-error"
            new_cmake = '''cmake -DCMAKE_BUILD_TYPE=Release \\
  -DCMAKE_C_COMPILER=gcc-14 \\
  -DCMAKE_CXX_COMPILER=g++-14 \\
  -DCMAKE_C_FLAGS="{march_flags}" \\
  -DCMAKE_CXX_FLAGS="{march_flags}" \\
  ../source'''

            if old_cmake in content and 'CMAKE_C_COMPILER=gcc-14' not in content:
                content = content.replace(old_cmake, new_cmake.format(march_flags=march_flags))
                patched = True
                print(f"  [OK] Added CMake GCC-14 patch for arch: {arch}")
            elif 'CMAKE_C_COMPILER=gcc-14' in content:
                print("  [INFO] CMake patch already applied")
            else:
                print("  [WARN] Could not find cmake command to patch")

            if patched:
                with open(install_sh_path, 'w') as f:
                    f.write(content)
                print("  [OK] install.sh patched successfully")
            else:
                print("  [INFO] install.sh already fully patched or no changes needed")

            return True

        except Exception as e:
            print(f"  [ERROR] Failed to patch install.sh: {e}")
            return False

    def install_benchmark(self):
        """
        Install x265-1.5.0 with GCC-14 native compilation.

        Note: x265 auto-detects CPU cores. Since THFix_in_compile=false and
        THChange_at_runtime=false, we don't control threads via environment
        variables. Instead, we use taskset at runtime to limit CPU visibility.
        """
        if not self.ensure_7z_available():
            print("  [ERROR] 7z is required to extract input files for x265")
            sys.exit(1)

        # --- Pre-download large files (Pattern 5) ---
        print("\\n>>> Checking for large files to pre-seed...")
        downloader = PreSeedDownloader()

        # [Pattern 5] Pre-download large files from downloads.xml (Size > 256MB)
        print("\\n>>> Checking for large files to pre-seed...")
        downloader = PreSeedDownloader()
        downloader.download_from_xml(self.benchmark_full, threshold_mb=96)

        print(f"\\n>>> Installing {self.benchmark_full}...")

        # Patch install.sh for GCC-14 and Ubuntu 24.04 compatibility
        self.patch_install_script()


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
        print(f"{'<'*80}\n")

        # Execute install command with real-time output streaming
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
            print(f"\n  [ERROR] Installation failed with return code {returncode}")
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
        """
        Run benchmark with specified thread count.

        Note: x265 auto-detects CPU cores. We use taskset to limit CPU visibility
        since THChange_at_runtime=false (x265 doesn't accept runtime thread arguments).
        """
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
        # Note: x265 auto-detects, so we don't pass NUM_CPU_CORES
        # Instead, we use taskset to limit visible CPUs
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

        # Always use taskset for x265 to control CPU visibility
        # x265 will auto-detect the CPUs available in the taskset mask
        cpu_list = self.get_cpu_affinity_list(num_threads)
        pts_base_cmd = f'taskset -c {cpu_list} phoronix-test-suite batch-run {self.benchmark_full}'
        cpu_info = f"CPU affinity (taskset): {cpu_list} - x265 will auto-detect {num_threads} CPU(s)"

        # CRITICAL: Environment variables MUST come BEFORE perf stat
        # Note: NO NUM_CPU_CORES for x265 (auto-detect only)
        if self.perf_events:
            if self.perf_paranoid <= 0:
                # Full monitoring mode: per-CPU stats + hardware counters
                pts_cmd = f'{batch_env} perf stat -e {self.perf_events} -A -a -o {perf_stats_file} {pts_base_cmd}'
                perf_mode = "Full (per-CPU + HW counters)"
            else:
                # Limited mode: aggregated events only (no -A -a)
                pts_cmd = f'{batch_env} perf stat -e {self.perf_events} -o {perf_stats_file} {pts_base_cmd}'
                perf_mode = "Limited (aggregated events only)"
        else:
            # No perf monitoring
            pts_cmd = f'{batch_env} {pts_base_cmd}'
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

        # Record CPU frequency after benchmark
        # Uses cross-platform method (works on x86_64, ARM64, and cloud VMs)
        print("\n[INFO] Recording CPU frequency after benchmark...")
        if self.record_cpu_frequency(freq_end_file):
            print("  [OK] End frequency recorded")
        else:
            print("  [WARN] CPU frequency not available (common on ARM64/cloud VMs)")

        # Check for PTS-reported test failures in the log (even if returncode is 0)
        pts_test_failed = False
        if log_file.exists():
            try:
                log_content = log_file.read_text(errors='ignore')
                # PTS reports failures with these messages
                failure_patterns = [
                    'quit with a non-zero exit status',
                    'failed to properly run',
                    'The following tests failed',
                ]
                for pattern in failure_patterns:
                    if pattern.lower() in log_content.lower():
                        pts_test_failed = True
                        print(f"\n[WARN] PTS reported test failure: '{pattern}' found in log")
                        break
            except Exception as e:
                print(f"  [WARN] Could not check log for failures: {e}")

        if returncode == 0 and not pts_test_failed:
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

        elif pts_test_failed:
            # PTS completed but some tests failed
            print("\n[WARN] Benchmark completed with some test failures")
            print(f"     Thread log: {log_file}")

            # Dump detailed error diagnostics for failed tests
            self.dump_error_diagnostics(num_threads, log_file)

            # Still try to parse perf stats
            try:
                perf_summary = self.parse_perf_stats_and_freq(
                    perf_stats_file,
                    freq_start_file,
                    freq_end_file,
                    cpu_list
                )
                with open(perf_summary_file, 'w') as f:
                    json.dump(perf_summary, f, indent=2)
            except Exception:
                pass  # Ignore perf parsing errors for failed tests

        else:
            print(f"\n[ERROR] Benchmark failed with return code {returncode}")
            err_file = self.results_dir / f"{num_threads}-thread.err"
            with open(err_file, 'w') as f:
                f.write(f"Benchmark failed with return code {returncode}\n")
                f.write(f"See {log_file} for details.\n")
            print(f"     Error log: {err_file}")

            # Dump detailed error diagnostics
            self.dump_error_diagnostics(num_threads, log_file)
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
            
            # Check if result exists, try both with dots (standard) and without dots (PTS sanitized)
            result_dir = pts_results_dir / result_name
            result_dir_nodots = pts_results_dir / result_name.replace('.', '')
            
            target_result_dir = None
            target_result_name = None
            
            if result_dir.exists():
                target_result_dir = result_dir
                target_result_name = result_name
            elif result_dir_nodots.exists():
                target_result_dir = result_dir_nodots
                target_result_name = result_name.replace('.', '')
            
            if not target_result_dir:
                print(f"[WARN] Result not found for {num_threads} threads: {result_name}")
                continue

            print(f"\n[INFO] Exporting results for {num_threads} thread(s)...")

            # Export to CSV
            csv_output = self.results_dir / f"{num_threads}-thread.csv"
            if csv_output.exists():
                print(f"  [SKIP] CSV already exists: {csv_output}")
            else:
                result = subprocess.run(
                    ['phoronix-test-suite', 'result-file-to-csv', target_result_name],
                    capture_output=True,
                    text=True
                )
                if result.returncode == 0:
                    home_csv = Path.home() / f"{target_result_name}.csv"
                    if home_csv.exists():
                        shutil.move(str(home_csv), str(csv_output))
                        print(f"  [OK] Saved: {csv_output}")
                    elif result.stdout.strip():
                        csv_output.write_text(result.stdout, encoding="utf-8")
                        print(f"  [OK] Saved stdout to: {csv_output}")

            # Export to JSON
            json_output = self.results_dir / f"{num_threads}-thread.json"
            if json_output.exists():
                print(f"  [SKIP] JSON already exists: {json_output}")
            else:
                result = subprocess.run(
                    ['phoronix-test-suite', 'result-file-to-json', target_result_name],
                    capture_output=True,
                    text=True
                )
                if result.returncode == 0:
                    home_json = Path.home() / f"{target_result_name}.json"
                    if home_json.exists():
                        shutil.move(str(home_json), str(json_output))
                        print(f"  [OK] Saved: {json_output}")
                    elif result.stdout.strip():
                        json_output.write_text(result.stdout, encoding="utf-8")
                        print(f"  [OK] Saved stdout to: {json_output}")

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

        # Generate summary.log
        with open(summary_log, 'w') as f:
            f.write("="*80 + "\n")
            f.write("x265 1.5.0 Benchmark Summary\n")
            f.write(f"Machine: {self.machine_name}\n")
            f.write(f"Test Category: {self.test_category}\n")
            f.write("="*80 + "\n\n")

            for result in all_results:
                f.write(f"Threads: {result['threads']}\n")
                f.write(f"  Test: {result['test_name']}\n")

                # Check for None to avoid f-string crash
                val_str = f"{result['value']:.2f}" if result['value'] is not None else "FAILED"
                f.write(f"  Average: {val_str} {result['unit']}\n")

                f.write("\n")

        print(f"[OK] Summary log saved: {summary_log}")

        # Generate summary.json
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
        """Main execution flow."""
        print(f"{'='*80}")
        print("x265 1.5.0 Benchmark Runner")
        print(f"{'='*80}")
        print(f"[INFO] Machine: {self.machine_name}")
        print(f"[INFO] vCPU count: {self.vcpu_count}")
        print("[INFO] Thread mode: Auto-detect (taskset-limited)")
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
        description="x265 1.5.0 Benchmark Runner",
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

    runner = X265Runner(threads_arg=threads, quick_mode=args.quick)
    success = runner.run()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
