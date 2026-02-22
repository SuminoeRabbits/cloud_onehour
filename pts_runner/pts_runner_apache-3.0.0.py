#!/usr/bin/env python3
"""
PTS Runner for apache-3.0.0

System Dependencies (from phoronix-test-suite info):
- Software Dependencies:
  * C/C++ Compiler Toolchain
  * Zlib
  * PERL
  * Perl Compatible Regular Expressions
  * Expat XML Parser Library
- Estimated Install Time: 143 Seconds
- Environment Size: 208 MB
- Test Type: System
- Supported Platforms: Linux, Solaris, BSD, MacOSX

Test Characteristics:
- Multi-threaded: No (single-threaded in this test configuration)
- Honors CFLAGS/CXXFLAGS: Yes
- Notable Instructions: N/A
- THFix_in_compile: false - Thread count NOT set at compile time
- THChange_at_runtime: false - Single-threaded benchmark (wrk for load testing)

Note: Apache HTTPD itself doesn't scale with vCPUs in this test configuration.
Always runs with 1 thread regardless of system vCPU count.
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
        """
        if not self.aria2_available:
            return False

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
                
                size_bytes = -1
                if filesize_node is not None and filesize_node.text:
                    try:
                        size_bytes = int(filesize_node.text.strip())
                    except ValueError:
                        pass
                
                if size_bytes <= 0:
                    size_bytes = self.get_remote_file_size(url)
                    
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
        try:
            cmd = ['curl', '-s', '-I', '-L', url]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0: return -1
            for line in result.stdout.splitlines():
                if line.lower().startswith('content-length:'):
                    return int(line.split(':')[1].strip())
        except Exception:
            pass
        return -1

    def ensure_file(self, url, filename):
        target_path = self.cache_dir / filename
        if target_path.exists():
            print(f"  [CACHE] File found: {filename}")
            return True

        print(f"  [ARIA2] Downloading {filename} with 16 connections...")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        cmd = ["aria2c", "-x", "16", "-s", "16", "-d", str(self.cache_dir), "-o", filename, url]
        
        try:
            subprocess.run(cmd, check=True)
            print(f"  [aria2c] Download completed: {filename}")
            return True
        except subprocess.CalledProcessError:
            print("  [WARN] aria2c download failed, falling back to PTS default")
            if target_path.exists(): target_path.unlink()
            return False


class ApacheRunner:
    def __init__(self, threads_arg=None, quick_mode=False):
        """
        Initialize Apache web server benchmark runner.

        **CRITICAL**: This is a single-threaded benchmark (THChange_at_runtime=false).
        Always runs with 1 thread regardless of arguments or system vCPU count.

        Args:
            threads_arg: Thread count argument (ignored for this single-threaded benchmark)
            quick_mode: If True, run tests once (FORCE_TIMES_TO_RUN=1) for development
        """
        self.benchmark = "apache-3.0.0"
        self.benchmark_full = f"pts/{self.benchmark}"
        self.test_category = "Network"
        # Replace spaces with underscores in test_category for directory name
        self.test_category_dir = self.test_category.replace(" ", "_")

        # System info
        self.vcpu_count = os.cpu_count() or 1
        self.machine_name = os.environ.get('MACHINE_NAME', os.uname().nodename)
        self.os_name = self.get_os_name()

        # CRITICAL: Single-threaded benchmark - always use thread_list = [1]
        # Ignore threads_arg parameter completely
        self.thread_list = [1]

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
            # Note: -a (system-wide) requires perf_event_paranoid <= 0
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
                    print("       Per-CPU metrics and hardware counters enabled")
                    print("       Full monitoring mode: perf stat -A -a")
                    return 0
                else:
                    print("  [ERROR] Failed to adjust perf_event_paranoid (sudo required)")
                    print("  [WARN] Running in LIMITED mode:")
                    print("         - No per-CPU metrics (no -A -a flags)")
                    print("         - No hardware counters (cycles, instructions)")
                    print("         - Software events only (aggregated)")
                    print("         - IPC calculation not available")
                    return current_value
            else:
                print(f"  [OK] perf_event_paranoid={current_value} is acceptable")
                print("       Full monitoring mode: perf stat -A -a")
                return current_value

        except Exception as e:
            print(f"  [ERROR] Could not check perf_event_paranoid: {e}")
            print("  [WARN] Assuming restrictive mode (perf_event_paranoid=2)")
            print("         Running in LIMITED mode without per-CPU metrics")
            return 2

    def dump_error_diagnostics(self, num_threads, log_file):
        """
        Dump diagnostic information when benchmark fails.

        Collects:
        1. PTS test-logs from ~/.phoronix-test-suite/test-results/
        2. Apache error logs (if available)
        3. Port 80/443 usage status
        4. Recent system dmesg output
        5. Installed test directory contents

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
            else:
                f.write(f"Installed test directory not found: {installed_dir}\n")

            # 3. Apache/httpd status and port check
            f.write("\n\n" + "-"*80 + "\n")
            f.write("[3] Apache/HTTP Service Status\n")
            f.write("-"*80 + "\n")

            # Check if apache2/httpd is running
            for service in ['apache2', 'httpd']:
                result = subprocess.run(
                    ['systemctl', 'status', service],
                    capture_output=True, text=True
                )
                if result.returncode == 0 or 'Active:' in result.stdout:
                    f.write(f"\n[systemctl status {service}]\n")
                    f.write(result.stdout[:2000])  # Limit output

            # Check port 80 usage
            f.write("\n[Port 80 usage (ss -tlnp)]\n")
            result = subprocess.run(
                ['ss', '-tlnp'],
                capture_output=True, text=True
            )
            for line in result.stdout.split('\n'):
                if ':80 ' in line or 'LISTEN' in line[:20]:
                    f.write(line + "\n")

            # 4. Apache error log (system)
            f.write("\n\n" + "-"*80 + "\n")
            f.write("[4] System Apache Error Logs\n")
            f.write("-"*80 + "\n")

            apache_logs = [
                '/var/log/apache2/error.log',
                '/var/log/httpd/error_log',
            ]
            for log_path in apache_logs:
                if Path(log_path).exists():
                    f.write(f"\n[{log_path} - last 50 lines]\n")
                    result = subprocess.run(
                        ['tail', '-50', log_path],
                        capture_output=True, text=True
                    )
                    f.write(result.stdout if result.stdout else "(empty or permission denied)\n")

            # 5. dmesg (last 30 lines)
            f.write("\n\n" + "-"*80 + "\n")
            f.write("[5] Recent dmesg Output\n")
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

            # 6. Memory and disk status
            f.write("\n\n" + "-"*80 + "\n")
            f.write("[6] System Resources\n")
            f.write("-"*80 + "\n")

            f.write("\n[free -h]\n")
            result = subprocess.run(['free', '-h'], capture_output=True, text=True)
            f.write(result.stdout)

            f.write("\n[df -h /]\n")
            result = subprocess.run(['df', '-h', '/'], capture_output=True, text=True)
            f.write(result.stdout)

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

        # Also clean the extracted source directories if they exist
        # This fixes the "ln: failed to create symbolic link 'libapr-1.so': File exists" error
        # that occurs when APR is partially built from a previous failed installation
        source_dirs_to_clean = [
            pts_home / 'installed-tests' / 'pts' / self.benchmark / 'httpd-2.4.56',
            pts_home / 'installed-tests' / 'pts' / self.benchmark / 'apr-1.7.2',
            pts_home / 'installed-tests' / 'pts' / self.benchmark / 'apr-util-1.6.3',
        ]
        for src_dir in source_dirs_to_clean:
            if src_dir.exists():
                print(f"  [CLEAN] Removing source dir: {src_dir}")
                shutil.rmtree(src_dir)

        print("  [OK] PTS cache cleaned")

    def get_cpu_affinity_list(self, n):
        """
        Generate CPU affinity list for HyperThreading optimization.

        Prioritizes physical cores (even IDs) first, then logical cores (odd IDs).
        Pattern: {0,2,4,...,1,3,5,...}

        Args:
            n: Number of threads

        Returns:
            Comma-separated CPU list string (e.g., "0,2,4,1,3")
        """
        half = self.vcpu_count // 2
        cpu_list = []

        if n <= half:
            # Physical cores only: 0,2,4,...
            cpu_list = [str(i * 2) for i in range(n)]
        else:
            # Physical cores + logical cores
            cpu_list = [str(i * 2) for i in range(half)]
            logical_count = n - half
            cpu_list.extend([str(i * 2 + 1) for i in range(logical_count)])

        return ','.join(cpu_list)

    def patch_install_script(self):
        """
        Patch the install.sh script to add GCC-14 and Ubuntu 24.04 compatibility fixes.

        This method modifies the test profile's install.sh to:
        1. Clean up existing httpd_ directory to prevent APR build failures
        2. Add 'no-asm' to OpenSSL build options (avoiding inline assembly errors with GCC-14)
        3. Pass XCFLAGS to make to suppress implicit-function-declaration errors in LuaJIT (ARM64)
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

            # Patch 0: Add cleanup of existing httpd_ directory to prevent APR build failures
            # The error "ln: failed to create symbolic link 'libapr-1.so': File exists" occurs
            # when httpd_ directory has partial files from a previous failed build
            cleanup_cmd = 'rm -rf $HOME/httpd_'
            if cleanup_cmd not in content:
                # Insert before 'mkdir $HOME/httpd_' line
                content = content.replace(
                    'mkdir $HOME/httpd_',
                    f"# Clean up existing httpd_ directory to prevent APR build failures\n{cleanup_cmd}\nmkdir $HOME/httpd_"
                )
                patched = True
                print("  [OK] Added httpd_ cleanup patch (fixes libapr-1.so symlink error)")
            else:
                print("  [INFO] httpd_ cleanup patch already applied")

            # Patch 1: Add 'no-asm' to OpenSSL build options (for GCC-14 compatibility)
            # This prevents inline assembly errors in OpenSSL 1.1.1i
            openssl_sed = "sed -i 's/OPENSSL_OPTS = no-shared no-psk no-srp no-dtls no-idea --prefix=$(abspath $(ODIR))/OPENSSL_OPTS = no-shared no-psk no-srp no-dtls no-idea no-asm --prefix=$(abspath $(ODIR))/' Makefile"
            if openssl_sed not in content:
                # Insert after 'cd wrk-4.2.0' line
                content = content.replace(
                    'cd wrk-4.2.0\n',
                    f"cd wrk-4.2.0\n# GCC-14 compatibility: Add no-asm to OpenSSL build options\n{openssl_sed}\n"
                )
                patched = True
                print("  [OK] Added OpenSSL no-asm patch")
            else:
                print("  [INFO] OpenSSL no-asm patch already applied")

            # Patch 2: Add XCFLAGS to wrk make command to suppress implicit-function-declaration errors
            # This is needed for LuaJIT on ARM64 with Ubuntu 24.04 where __clear_cache is implicitly declared
            # XCFLAGS is passed through to LuaJIT's build system
            #
            # Strategy: Find the 'make -j $NUM_CPU_CORES' line that comes AFTER 'cd wrk-4.2.0'
            # and add XCFLAGS to it. This is the wrk build, not the httpd build.

            if 'XCFLAGS="-Wno-error=implicit-function-declaration"' not in content:
                # Use regex to find and replace the make command in the wrk section
                # Pattern: After 'cd wrk-4.2.0' section, find 'make -j $NUM_CPU_CORES' (without XCFLAGS)
                # This handles cases where the line may or may not have a sed command before it

                # Split content and process line by line for precise control
                lines = content.split('\n')
                in_wrk_section = False
                xcflags_patched = False
                new_lines = []

                for i, line in enumerate(lines):
                    # Detect entering wrk section
                    if 'cd wrk-4.2.0' in line:
                        in_wrk_section = True

                    # In wrk section, find the make command (not gmake, not with XCFLAGS already)
                    if in_wrk_section and not xcflags_patched:
                        # Match 'make -j $NUM_CPU_CORES' at start of line (possibly with leading whitespace)
                        # but NOT 'gmake' and NOT already containing XCFLAGS
                        stripped = line.strip()
                        if stripped == 'make -j $NUM_CPU_CORES':
                            # Preserve original indentation
                            indent = line[:len(line) - len(line.lstrip())]
                            line = f'{indent}make -j $NUM_CPU_CORES XCFLAGS="-Wno-error=implicit-function-declaration"'
                            xcflags_patched = True
                            print("  [OK] Added XCFLAGS patch for LuaJIT ARM64 compatibility")

                    new_lines.append(line)

                if xcflags_patched:
                    content = '\n'.join(new_lines)
                    patched = True
                else:
                    print("  [WARN] Could not find wrk make command to patch for XCFLAGS")
            else:
                print("  [INFO] XCFLAGS patch already applied")

            if patched:
                with open(install_sh_path, 'w') as f:
                    f.write(content)
                print("  [OK] install.sh patched successfully")
            else:
                print("  [INFO] install.sh already fully patched")

            return True

        except Exception as e:
            print(f"  [ERROR] Failed to patch install.sh: {e}")
            return False

    def install_benchmark(self):
        """
        Install apache-3.0.0 with GCC-14 and Ubuntu 24.04 compatibility workarounds.

        Note: Since THChange_at_runtime=false, this is a single-threaded benchmark.
        No runtime thread configuration is needed; Apache always runs with 1 thread.

        Since THFix_in_compile=false, NUM_CPU_CORES is NOT set during build.
        Apache doesn't use NUM_CPU_CORES for this workload.

        Compatibility Fixes:
        1. GCC-14/OpenSSL Fix:
           - Automatically patches install.sh to add 'no-asm' to OpenSSL build options
           - Avoids "expected ')' before ':' token" errors in crypto/bn/asm/x86_64-gcc.c
           - Slightly slower than assembly version, but enables GCC-14 compilation

        2. Ubuntu 24.04/LuaJIT Fix:
           - Adds '-Wno-error=implicit-function-declaration' to LuaJIT CFLAGS
           - Fixes "__clear_cache implicit declaration" errors on ARM64 with Ubuntu 24.04
           - Ubuntu 24.04 treats implicit function declarations as errors by default
        """
        print(f"\n>>> Installing {self.benchmark_full}...")

        # Remove existing installation first
        print("  [INFO] Removing existing installation...")
        remove_cmd = f'echo "y" | phoronix-test-suite remove-installed-test "{self.benchmark_full}"'
        print(f"  [INSTALL CMD] {remove_cmd}")
        subprocess.run(
            ['bash', '-c', remove_cmd],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        # Patch install.sh for GCC-14 and Ubuntu 24.04 compatibility
        self.patch_install_script()

        # Build install command with environment variables
        # Note: NUM_CPU_CORES is NOT set because this is single-threaded (THChange_at_runtime=false)
        # Use batch-install to suppress prompts
        # MAKEFLAGS: parallelize compilation itself with -j$(nproc)
        #
        # Compatibility Workarounds Applied by patch_install_script():
        # 1. GCC-14/OpenSSL: Adds 'no-asm' to OpenSSL build options in wrk Makefile
        #    - Disables inline assembly in OpenSSL 1.1.1i which has issues with GCC-14
        # 2. Ubuntu 24.04/LuaJIT: Adds '-Wno-error=implicit-function-declaration' to CFLAGS
        #    - Fixes "__clear_cache implicit declaration" errors on ARM64
        # Performance impact is minimal for this single-threaded Apache benchmark
        nproc = os.cpu_count() or 1
        install_cmd = f'MAKEFLAGS="-j{nproc}" CC=gcc-14 CXX=g++-14 CFLAGS="-O3 -march=native -mtune=native" CXXFLAGS="-O3 -march=native -mtune=native" phoronix-test-suite batch-install {self.benchmark_full}'

        # Print install command for debugging (as per README requirement)
        print(f"\n{'>'*80}")
        print("[PTS INSTALL COMMAND]")
        print(f"  {install_cmd}")
        print(f"{'<'*80}\n")

        # Execute install command with real-time output streaming
        print("[INFO] Starting installation (this may take a few minutes)...")
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

        # CRITICAL: Verify httpd was actually built
        # The Apache benchmark requires httpd binary in httpd_/bin/httpd
        httpd_binary = installed_dir / 'httpd_' / 'bin' / 'httpd'
        httpd_dir = installed_dir / 'httpd_'

        if not httpd_dir.exists() or not any(httpd_dir.iterdir()):
            print("  [ERROR] Apache httpd build failed!")
            print(f"  [ERROR] httpd_ directory is empty or missing: {httpd_dir}")
            print("  [INFO] This is usually caused by APR library build failure")
            print("  [INFO] Check install.log for 'ln: failed to create symbolic link' errors")
            print("  [INFO] Possible fixes:")
            print(f"         1. Clean cache: rm -rf ~/.phoronix-test-suite/installed-tests/pts/{self.benchmark}")
            print(f"         2. Reinstall: phoronix-test-suite install {self.benchmark_full}")
            sys.exit(1)

        if not httpd_binary.exists():
            print(f"  [ERROR] Apache httpd binary not found: {httpd_binary}")
            print("  [INFO] httpd compilation may have failed silently")
            # List what's in httpd_ for debugging
            print(f"  [DEBUG] Contents of {httpd_dir}:")
            try:
                for item in httpd_dir.iterdir():
                    print(f"           {item.name}")
            except Exception:
                print("           (could not list directory)")
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
        print(f"  [OK] Apache httpd binary found: {httpd_binary}")

    def patch_apache_script(self, num_threads):
        """
        Patch the apache execution script to use specified number of wrk threads.
        
        The default apache script uses: ./wrk-4.2.0/wrk -t $NUM_CPU_CORES ...
        This causes wrk to use all system threads (12 in this case), which fails
        when concurrent connections < threads (e.g., -c 4 with -t 12).
        
        We modify it to use: ./wrk-4.2.0/wrk -t <num_threads> ...
        where num_threads is min(num_threads, concurrent_requests).
        
        Args:
            num_threads: Number of threads specified by user (1 for single-threaded mode)
        """
        pts_home = Path.home() / '.phoronix-test-suite'
        apache_script = pts_home / 'installed-tests' / 'pts' / self.benchmark / 'apache'
        
        if not apache_script.exists():
            print(f"  [ERROR] Apache script not found: {apache_script}")
            return False
            
        print(f"\n>>> Patching apache script for {num_threads} thread(s)")
        
        try:
            with open(apache_script, 'r') as f:
                content = f.read()
            
            # Replace '-t $NUM_CPU_CORES' with '-t <num_threads>'
            # Original: ./wrk-4.2.0/wrk -t $NUM_CPU_CORES $@ > $LOG_FILE 2>&1
            # Modified: ./wrk-4.2.0/wrk -t <num_threads> $@ > $LOG_FILE 2>&1
            original_line = './wrk-4.2.0/wrk -t $NUM_CPU_CORES $@ > $LOG_FILE 2>&1'
            
            if original_line in content:
                # Use num_threads directly for wrk threads
                new_line = f'./wrk-4.2.0/wrk -t {num_threads} $@ > $LOG_FILE 2>&1'
                content = content.replace(original_line, new_line)
                
                with open(apache_script, 'w') as f:
                    f.write(content)
                    
                print(f"  [OK] Patched apache script: wrk will use {num_threads} thread(s)")
                print("  [INFO] This ensures wrk threads <= concurrent connections")
                return True
            else:
                # Already patched or different format
                print("  [WARN] Apache script has unexpected format, checking if already patched...")
                if f'-t {num_threads}' in content:
                    print(f"  [INFO] Script already patched for {num_threads} thread(s)")
                    return True
                else:
                    print("  [ERROR] Could not patch apache script - unexpected format")
                    print(f"  [DEBUG] Content: {content}")
                    return False
                    
        except Exception as e:
            print(f"  [ERROR] Failed to patch apache script: {e}")
            return False

    def parse_perf_stats_and_freq(self, perf_stats_file, freq_start_file, freq_end_file, cpu_list):
        """
        Parse perf stat output and CPU frequency files to generate performance summary.

        Args:
            perf_stats_file: Path to perf stat output file
            freq_start_file: Path to start frequency file
            freq_end_file: Path to end frequency file
            cpu_list: String of CPU IDs used (e.g., "0,2,4")

        Returns:
            dict: Performance summary containing per-CPU metrics
        """
        print("\n>>> Parsing perf stats and frequency data")
        print(f"  [INFO] perf stats file: {perf_stats_file}")
        print(f"  [INFO] freq start file: {freq_start_file}")
        print(f"  [INFO] freq end file: {freq_end_file}")
        print(f"  [INFO] cpu list: {cpu_list}")

        # Parse CPU list to get individual CPU IDs
        cpu_ids = [int(c.strip()) for c in cpu_list.split(',')]
        print(f"  [DEBUG] Parsed CPU IDs: {cpu_ids}")

        # Initialize data structures for per-CPU metrics
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

        # Parse perf stat output file
        print("  [INFO] Parsing perf stat output...")
        try:
            with open(perf_stats_file, 'r') as f:
                perf_content = f.read()
                print(f"  [DEBUG] perf stat file size: {len(perf_content)} bytes")

                # Parse per-CPU metrics (format: "CPU<n>   <value>   <event>")
                # Example: "CPU0                123456789      cycles"
                for line in perf_content.split('\n'):
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

                        # Map event names to our data structure
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

            print(f"  [OK] Parsed perf stat data for {len(per_cpu_metrics)} CPUs")

        except Exception as e:
            print(f"  [ERROR] Failed to parse perf stat file: {e}")
            raise

        # Parse frequency files
        print("  [INFO] Parsing frequency files...")
        freq_start = {}
        freq_end = {}

        try:
            # Read start frequencies (format: one frequency per line in kHz)
            with open(freq_start_file, 'r') as f:
                lines = f.read().strip().split('\n')
                for i, line in enumerate(lines):
                    if line.strip():
                        freq_start[i] = float(line.strip())
            print(f"  [DEBUG] Read {len(freq_start)} start frequencies")

            # Read end frequencies
            with open(freq_end_file, 'r') as f:
                lines = f.read().strip().split('\n')
                for i, line in enumerate(lines):
                    if line.strip():
                        freq_end[i] = float(line.strip())
            print(f"  [DEBUG] Read {len(freq_end)} end frequencies")

        except Exception as e:
            print(f"  [ERROR] Failed to parse frequency files: {e}")
            raise

        # Calculate metrics
        print("  [INFO] Calculating performance metrics...")
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

            # avg_frequency_ghz = cycles / (cpu-clock / 1000) / 1e9
            if metrics['cpu_clock'] > 0:
                avg_freq = metrics['cycles'] / (metrics['cpu_clock'] / 1000.0) / 1e9
                perf_summary['avg_frequency_ghz'][str(cpu_id)] = round(avg_freq, 3)
            else:
                perf_summary['avg_frequency_ghz'][str(cpu_id)] = 0.0

            # start_frequency_ghz = freq_start[cpu] / 1,000,000 (kHz to GHz)
            if cpu_id in freq_start:
                start_freq = freq_start[cpu_id] / 1_000_000.0
                perf_summary['start_frequency_ghz'][str(cpu_id)] = round(start_freq, 3)
            else:
                perf_summary['start_frequency_ghz'][str(cpu_id)] = 0.0

            # end_frequency_ghz = freq_end[cpu] / 1,000,000 (kHz to GHz)
            if cpu_id in freq_end:
                end_freq = freq_end[cpu_id] / 1_000_000.0
                perf_summary['end_frequency_ghz'][str(cpu_id)] = round(end_freq, 3)
            else:
                perf_summary['end_frequency_ghz'][str(cpu_id)] = 0.0

            # ipc = instructions / cycles
            if metrics['cycles'] > 0:
                ipc = metrics['instructions'] / metrics['cycles']
                perf_summary['ipc'][str(cpu_id)] = round(ipc, 2)
            else:
                perf_summary['ipc'][str(cpu_id)] = 0.0

            # Store raw values
            perf_summary['total_cycles'][str(cpu_id)] = int(metrics['cycles'])
            perf_summary['total_instructions'][str(cpu_id)] = int(metrics['instructions'])

            # Track task-clock for utilization calculation
            total_task_clock += metrics['task_clock']
            max_task_clock = max(max_task_clock, metrics['task_clock'])

        # Calculate elapsed time (use max task-clock as elapsed time in ms)
        if max_task_clock > 0:
            perf_summary['elapsed_time_sec'] = round(max_task_clock / 1000.0, 2)

        # Calculate CPU utilization (total task-clock / elapsed_time / num_cpus * 100)
        # This represents the average CPU utilization across all CPUs
        if max_task_clock > 0:
            utilization = (total_task_clock / max_task_clock / len(cpu_ids)) * 100.0
            perf_summary['cpu_utilization_percent'] = round(utilization, 1)

        print("  [OK] Performance metrics calculated")
        print(f"  [DEBUG] Elapsed time: {perf_summary['elapsed_time_sec']} sec")
        print(f"  [DEBUG] CPU utilization: {perf_summary['cpu_utilization_percent']}%")

        return perf_summary

    def run_benchmark(self, num_threads):
        """
        Run benchmark with specified thread count.

        Note: For Apache (single-threaded), num_threads will always be 1.

        Args:
            num_threads: Number of threads to use (always 1 for this benchmark)
        """
        print(f"\n{'='*80}")
        print(f">>> Running benchmark with {num_threads} thread(s)")
        print(f"{'='*80}")

        # Create output directory
        self.results_dir.mkdir(parents=True, exist_ok=True)
        log_file = self.results_dir / f"{num_threads}-thread.log"
        stdout_log = self.results_dir / "stdout.log"

        # Define file paths for perf stats and frequency monitoring
        perf_stats_file = self.results_dir / f"{num_threads}-thread_perf_stats.txt"
        freq_start_file = self.results_dir / f"{num_threads}-thread_freq_start.txt"
        freq_end_file = self.results_dir / f"{num_threads}-thread_freq_end.txt"
        perf_summary_file = self.results_dir / f"{num_threads}-thread_perf_summary.json"

        # Build PTS command
        # Since this is single-threaded, we always use CPU 0 with taskset
        # Apache doesn't scale with vCPUs in this test, so affinity is just for consistency

        # Environment variables to suppress all prompts
        # BATCH_MODE, SKIP_ALL_PROMPTS: additional safeguards
        # TEST_RESULTS_NAME, TEST_RESULTS_IDENTIFIER: auto-generate result names
        # DISPLAY_COMPACT_RESULTS: suppress "view text results" prompt
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

        # Patch apache script to use correct number of wrk threads
        # This prevents "number of connections must be >= threads" errors
        if not self.patch_apache_script(num_threads):
            print("  [ERROR] Failed to patch apache script")
            return False

        # Single-threaded: use CPU 0 only with taskset
        cpu_list = '0'
        pts_base_cmd = f'taskset -c {cpu_list} phoronix-test-suite batch-run {self.benchmark_full}'
        cpu_info = f"Single-threaded benchmark: CPU affinity (taskset): {cpu_list}"

        # Wrap PTS command with perf stat
        # CRITICAL: Environment variables MUST come BEFORE perf stat (README)
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

        # Print PTS command to stdout for debugging (as per README requirement)
        print(f"\n{'>'*80}")
        print("[PTS BENCHMARK COMMAND]")
        print(f"  {pts_cmd}")
        print(f"  {cpu_info}")
        print("  Output:")
        print(f"    Thread log: {log_file}")
        print(f"    Stdout log: {stdout_log}")
        print(f"    Perf stats: {perf_stats_file}")
        print(f"    Freq start: {freq_start_file}")
        print(f"    Freq end: {freq_end_file}")
        print(f"    Perf summary: {perf_summary_file}")
        print(f"{'<'*80}\n")

        # Record CPU frequency before benchmark
        # Uses cross-platform method (works on x86_64, ARM64, and cloud VMs)
        print("[INFO] Recording CPU frequency before benchmark...")
        if self.record_cpu_frequency(freq_start_file):
            print("  [OK] Start frequency recorded")
        else:
            print("  [WARN] CPU frequency not available (common on ARM64/cloud VMs)")

        # Execute with tee-like behavior: output to both terminal and log files
        with open(log_file, 'w') as log_f, open(stdout_log, 'a') as stdout_f:
            # Write command header to stdout.log
            stdout_f.write(f"\n{'='*80}\n")
            stdout_f.write(f"[PTS BENCHMARK COMMAND - {num_threads} thread(s)]\n")
            stdout_f.write(f"{pts_cmd}\n")
            stdout_f.write(f"{cpu_info}\n")
            stdout_f.write(f"{'='*80}\n\n")
            stdout_f.flush()

            # Run PTS command with real-time output streaming
            process = subprocess.Popen(
                ['bash', '-c', pts_cmd],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )

            # Stream output to terminal, thread-specific log, and cumulative stdout.log
            for line in process.stdout:
                print(line, end='')  # Terminal output
                log_f.write(line)    # Thread-specific log file
                stdout_f.write(line) # Cumulative stdout.log
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
            print(f"     Thread log: {log_file}")
            print(f"     Stdout log: {stdout_log}")

            # Parse perf stats and save summary
            try:
                perf_summary = self.parse_perf_stats_and_freq(
                    perf_stats_file,
                    freq_start_file,
                    freq_end_file,
                    cpu_list
                )

                # Save perf summary to JSON
                with open(perf_summary_file, 'w') as f:
                    json.dump(perf_summary, f, indent=2)
                print(f"     Perf summary: {perf_summary_file}")

            except Exception as e:
                print(f"  [ERROR] Failed to parse perf stats: {e}")
                print("  [INFO] Benchmark results are still valid, continuing...")

        elif pts_test_failed:
            # PTS completed but some tests failed
            print("\n[WARN] Benchmark completed with some test failures")
            print(f"     Thread log: {log_file}")
            print(f"     Stdout log: {stdout_log}")

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
            print(f"  [EXPORT] CSV: {csv_output}")
            result = subprocess.run(
                ['phoronix-test-suite', 'result-file-to-csv', target_result_name],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                # PTS saves to ~/result_name.csv, move it to our results directory
                home_csv = Path.home() / f"{target_result_name}.csv"
                if home_csv.exists():
                    shutil.move(str(home_csv), str(csv_output))
                    print(f"  [OK] Saved: {csv_output}")
            else:
                print(f"  [WARN] CSV export failed: {result.stderr}")

            # Export to JSON
            json_output = self.results_dir / f"{num_threads}-thread.json"
            print(f"  [EXPORT] JSON: {json_output}")
            result = subprocess.run(
                ['phoronix-test-suite', 'result-file-to-json', target_result_name],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                # PTS saves to ~/result_name.json, move it to our results directory
                home_json = Path.home() / f"{target_result_name}.json"
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
            f.write("Apache Web Server Benchmark Summary\n")
            f.write(f"Machine: {self.machine_name}\n")
            f.write(f"Test Category: {self.test_category}\n")
            f.write("Note: Single-threaded benchmark\n")
            f.write("="*80 + "\n\n")

            for result in all_results:
                f.write(f"Threads: {result['threads']}\n")
                f.write(f"  Test: {result['test_name']}\n")
                f.write(f"  Description: {result['description']}\n")

                # Check for None to avoid f-string crash
                val_str = f"{result['value']:.2f}" if result['value'] is not None else "FAILED"
                f.write(f"  Average: {val_str} {result['unit']}\n")

                # Handle raw values safely
                raw_vals = result.get('raw_values')
                if raw_vals:
                    val_str = ', '.join([f'{v:.2f}' for v in raw_vals if v is not None])
                    f.write(f"  Raw values: {val_str}\n")
                else:
                    f.write("  Raw values: N/A\n")

                f.write("\n")

            f.write("="*80 + "\n")
            f.write("Summary Table\n")
            f.write("="*80 + "\n")
            f.write(f"{'Threads':<10} {'Average':<15} {'Unit':<20}\n")
            f.write("-"*80 + "\n")
            for result in all_results:
                val_str = f"{result['value']:.2f}" if result['value'] is not None else "None"
                f.write(f"{result['threads']:<10} {val_str:<15} {result['unit']:<20}\n")

        print(f"[OK] Summary log saved: {summary_log}")

        # Generate summary.json (AI-friendly format)
        summary_data = {
            "benchmark": self.benchmark,
            "test_category": self.test_category,
            "machine": self.machine_name,
            "vcpu_count": self.vcpu_count,
            "single_threaded": True,
            "results": all_results
        }

        with open(summary_json_file, 'w') as f:
            json.dump(summary_data, f, indent=2)

        print(f"[OK] Summary JSON saved: {summary_json_file}")

    def run(self):
        """Main execution flow."""
        print(f"{'='*80}")
        print("Apache Web Server Benchmark Runner")
        print(f"{'='*80}")
        print(f"[INFO] Machine: {self.machine_name}")
        print(f"[INFO] vCPU count: {self.vcpu_count}")
        print(f"[INFO] Test category: {self.test_category}")
        print("[INFO] Thread mode: Single-threaded (THChange_at_runtime=false)")
        print(f"[INFO] Threads to test: {self.thread_list}")
        print(f"[INFO] Results directory: {self.results_dir}")
        print("[INFO] Note: Apache is single-threaded; uses wrk for load testing")
        print()

        # Clean existing results directory before starting
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

        # Install benchmark once (not per thread count, since single-threaded)
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

        # Run for each thread count (should only be 1)
        failed = []
        for num_threads in self.thread_list:
            # Run benchmark
            if not self.run_benchmark(num_threads):
                failed.append(num_threads)

        # Export results to CSV and JSON
        self.export_results()

        # Generate summary
        self.generate_summary()
        cleanup_pts_artifacts(self.benchmark)

        # Summary
        print(f"\n{'='*80}")
        print("Benchmark Summary")
        print(f"{'='*80}")
        print(f"Total tests: {len(self.thread_list)}")
        print(f"Successful: {len(self.thread_list) - len(failed)}")
        print(f"Failed: {len(failed)}")
        if failed:
            print(f"Failed thread counts: {failed}")
        print(f"{'='*80}")

        return len(failed) == 0


def main():
    parser = argparse.ArgumentParser(
        description="Apache Web Server Benchmark Runner (Single-threaded)",
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

    runner = ApacheRunner(threads_arg=threads, quick_mode=args.quick)
    success = runner.run()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
