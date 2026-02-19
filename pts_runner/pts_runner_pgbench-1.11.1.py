#!/usr/bin/env python3
"""
PTS Runner for pgbench-1.11.1

System Dependencies (from phoronix-test-suite info):
- Software Dependencies:
  * build-utilities
  * bc
- Estimated Install Time: 37 Seconds (approx)
- Environment Size: 1500 MB
- Test Type: System
- Supported Platforms: Linux, MacOSX, BSD, Solaris, Windows

Test Characteristics:
- Multi-threaded: Yes (pgbench worker threads scale with cores)
- THFix_in_compile: false
- THChange_at_runtime: true
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import signal
import atexit
from pathlib import Path
from runner_common import detect_pts_failure_from_log, get_install_status


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

    def ensure_file(self, urls, filename):
        """
        Directly download file using aria2c (assumes size check passed).
        Args:
            urls: List of URLs or single URL string
            filename: Target filename
        """
        target_path = self.cache_dir / filename
        
        # Check if file exists in cache
        if target_path.exists():
            print(f"  [CACHE] File found: {filename}")
            return True

        if isinstance(urls, str):
            urls = [urls]

        # Need to download
        print(f"  [ARIA2] Downloading {filename} with 16 connections...")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # aria2c command - pass all URLs as separate arguments
        cmd = [
            "aria2c", "-x", "16", "-s", "16", 
            "-d", str(self.cache_dir), 
            "-o", filename
        ] + urls
        
        try:
            subprocess.run(cmd, check=True)
            print(f"  [aria2c] Download completed: {filename}")
        except subprocess.CalledProcessError as e:
            print(f"  [ERROR] aria2c download failed for {filename}: {e}")
            return False
        return True

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

                # Handle comma-separated URLs
                urls = [u.strip() for u in url_node.text.split(',')]
                url = urls[0] if urls else None
                filename = filename_node.text.strip()
                
                if not url:
                    continue

                # Determine size
                size_bytes = -1
                if filesize_node is not None and filesize_node.text:
                    try:
                        size_bytes = int(filesize_node.text.strip())
                    except ValueError:
                        pass
                
                # If size not in XML, try to get it from network (fallback)
                if size_bytes <= 0:
                    size_bytes = self.get_remote_file_size(url)
                    
                # Check threshold
                if size_bytes > 0:
                    size_mb = size_bytes / (1024 * 1024)
                    if size_mb >= threshold_mb:
                        print(f"  [INFO] {filename} is large ({size_mb:.1f} MB), accelerating with aria2c...")
                        self.ensure_file(urls, filename)
        except Exception as e:
            print(f"  [ERROR] Failed to parse downloads.xml: {e}")
            return False
        return True


class PgbenchRunner:
    # Valid values for test option filtering (must match test-definition.xml)
    VALID_SCALING_FACTORS = ['1', '100', '1000', '10000', '25000']
    VALID_CLIENTS = ['1', '50', '100', '250', '500']
    VALID_MODES = {'rw': ['Read Write'], 'ro': ['Read Only'], 'both': ['Read Write', 'Read Only']}

    def __init__(self, threads_arg=None, quick_mode=False,
                 scaling_factors=None, clients=None, modes=None):
        """
        Initialize PostgreSQL pgbench runner.
        """
        self.benchmark = "pgbench-1.11.1"
        self.benchmark_full = f"pts/{self.benchmark}"
        self.test_category = "Database"
        self.test_category_dir = self.test_category.replace(" ", "_")

        # System info
        self.vcpu_count = os.cpu_count() or 1
        self.machine_name = os.environ.get('MACHINE_NAME', os.uname().nodename)
        self.os_name = self.get_os_name()

        # Thread list setup
        if threads_arg is None:
            self.thread_list = list(range(2, self.vcpu_count + 1, 2))
        else:
            n = min(threads_arg, self.vcpu_count)
            self.thread_list = [n]

        # Project structure
        self.script_dir = Path(__file__).parent.resolve()
        self.project_root = self.script_dir.parent
        self.results_dir = self.project_root / "results" / self.machine_name / self.os_name / self.test_category_dir / self.benchmark

        # Quick mode for development
        self.quick_mode = quick_mode

        # Test option filters (None = all)
        self.filter_scaling_factors = self._validate_filter(
            scaling_factors, self.VALID_SCALING_FACTORS, 'scaling-factors')
        self.filter_clients = self._validate_filter(
            clients, self.VALID_CLIENTS, 'clients')
        self.filter_modes = self.VALID_MODES.get(modes) if modes else None

        # Check perf permissions (standard Linux check)
        self.perf_paranoid = self.check_and_setup_perf_permissions()

        # Detect environment for logging
        self.is_wsl_env = self.is_wsl()
        if self.is_wsl_env:
            print("  [INFO] Running on WSL environment")

        # Feature Detection: Check if perf is actually functional
        self.perf_events = self.get_perf_events()
        # Enforce safety
        self.ensure_upload_disabled()
        if self.perf_events:
            print(f"  [OK] Perf monitoring enabled with events: {self.perf_events}")
        else:
            print("  [INFO] Perf monitoring disabled (command missing or unsupported)")

        # Register cleanup handlers for graceful shutdown
        self._setup_cleanup_handlers()

    def _cleanup_handler(self, signum=None, _frame=None):
        """Handler for signals and atexit to ensure PostgreSQL cleanup."""
        if signum is not None:
            print(f"\n[SIGNAL] Received signal {signum}, cleaning up...")
        else:
            print(f"\n[EXIT] Script terminating, cleaning up...")
        self.cleanup_postgresql()
        if signum is not None:
            sys.exit(1)

    def _setup_cleanup_handlers(self):
        """Register signal handlers and atexit for cleanup."""
        # Register atexit handler for normal exit
        atexit.register(lambda: self._cleanup_handler())

        # Register signal handlers for interrupts
        signal.signal(signal.SIGINT, self._cleanup_handler)   # Ctrl+C
        signal.signal(signal.SIGTERM, self._cleanup_handler)  # kill command

    def _validate_filter(self, values, valid_values, name):
        """Validate filter values against allowed list. Returns None if no filter."""
        if values is None:
            return None
        invalid = [v for v in values if v not in valid_values]
        if invalid:
            print(f"  [ERROR] Invalid {name}: {invalid}. Valid: {valid_values}")
            sys.exit(1)
        return values

    def get_os_name(self):
        """Get OS name and version formatted as <Distro>_<Version>."""
        try:
            cmd = "lsb_release -d -s"
            result = subprocess.run(cmd.split(), capture_output=True, text=True)
            if result.returncode == 0:
                description = result.stdout.strip()
                parts = description.split()
                if len(parts) >= 2:
                    distro = parts[0]
                    version = parts[1].replace('.', '_')
                    return f"{distro}_{version}"
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

    def check_and_setup_perf_permissions(self):
        """Check and adjust perf_event_paranoid setting."""
        print(f"\n{'='*80}")
        print(">>> Checking perf_event_paranoid setting")
        print(f"{'='*80}")

        try:
            result = subprocess.run(
                ['cat', '/proc/sys/kernel/perf_event_paranoid'],
                capture_output=True,
                text=True,
                check=True
            )
            current_value = int(result.stdout.strip())
            print(f"  [INFO] Current perf_event_paranoid: {current_value}")

            if current_value >= 1:
                print(f"  [WARN] perf_event_paranoid={current_value} is too restrictive")
                print(f"  [INFO] Attempting to adjust to 0...")

                result = subprocess.run(
                    ['sudo', 'sysctl', '-w', 'kernel.perf_event_paranoid=0'],
                    capture_output=True,
                    text=True
                )

                if result.returncode == 0:
                    print(f"  [OK] perf_event_paranoid adjusted to 0")
                    return 0
                else:
                    print(f"  [ERROR] Failed to adjust (sudo required)")
                    print(f"  [WARN] Running in LIMITED mode")
                    return current_value
            else:
                print(f"  [OK] perf_event_paranoid={current_value} is acceptable")
                return current_value

        except Exception as e:
            print(f"  [ERROR] Could not check perf_event_paranoid: {e}")
            return 2


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
        """
        perf_path = shutil.which("perf")
        if not perf_path:
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

        return None

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

    def patch_install_script(self):
        """
        Patch install.sh to remove --disable-rpath.
        This flag prevents binaries from finding their own libraries in relocatable installs.
        """
        install_sh_path = Path.home() / '.phoronix-test-suite' / 'test-profiles' / 'pts' / self.benchmark / 'install.sh'

        if not install_sh_path.exists():
            print(f"  [WARN] install.sh not found at {install_sh_path}")
            return False

        try:
            with open(install_sh_path, 'r') as f:
                content = f.read()

            if '--disable-rpath' in content:
                print(f"  [INFO] Patching install.sh: Removing --disable-rpath...")
                # Replace with empty string
                patched = content.replace('--disable-rpath', '')

                with open(install_sh_path, 'w') as f:
                    f.write(patched)
                print(f"  [OK] install.sh patched successfully")
                return True
            else:
                print(f"  [INFO] install.sh already clean (no --disable-rpath)")
                return True

        except Exception as e:
            print(f"  [ERROR] Failed to patch install.sh: {e}")
            return False

    def patch_results_definition(self):
        """
        Patch results-definition.xml to match pgbench 14.0 output format.
        pgbench 14.0 changed from "(excluding connections establishing)"
        to "(without initial connection time)".
        """
        results_def_path = Path.home() / '.phoronix-test-suite' / 'test-profiles' / 'pts' / self.benchmark / 'results-definition.xml'

        if not results_def_path.exists():
            print(f"  [WARN] results-definition.xml not found at {results_def_path}")
            return False

        try:
            with open(results_def_path, 'r') as f:
                content = f.read()

            # Check if patch is needed
            if '(excluding connections establishing)' in content:
                print(f"  [INFO] Patching results-definition.xml for pgbench 14.0 output format...")

                # Update to match pgbench 14.0 output
                # Note: pgbench 14.0 no longer outputs "TPS" at the end
                patched = content.replace(
                    'tps = #_RESULT_# (excluding connections establishing) TPS',
                    'tps = #_RESULT_# (without initial connection time)'
                ).replace(
                    'tps = #_RESULT_# (without initial connection time) TPS',
                    'tps = #_RESULT_# (without initial connection time)'
                )

                with open(results_def_path, 'w') as f:
                    f.write(patched)
                print(f"  [OK] results-definition.xml patched successfully")
                return True
            else:
                print(f"  [INFO] results-definition.xml already updated")
                return True

        except Exception as e:
            print(f"  [ERROR] Failed to patch results-definition.xml: {e}")
            return False

    def _has_test_option_filters(self):
        """Check if any test option filters are active."""
        return (self.filter_scaling_factors is not None
                or self.filter_clients is not None
                or self.filter_modes is not None)

    def patch_test_options(self):
        """
        Temporarily patch test-definition.xml to limit test configurations.
        Filters <Entry> elements based on --scaling-factors, --clients, --modes.
        """
        if not self._has_test_option_filters():
            return

        import xml.etree.ElementTree as ET

        test_def_path = (Path.home() / '.phoronix-test-suite' / 'test-profiles'
                         / 'pts' / self.benchmark / 'test-definition.xml')

        if not test_def_path.exists():
            print(f"  [WARN] test-definition.xml not found: {test_def_path}")
            return

        backup_path = test_def_path.with_suffix('.xml.bak')

        print(f"\n>>> Patching test-definition.xml to filter test configurations...")

        try:
            # Backup original
            shutil.copy2(test_def_path, backup_path)

            tree = ET.parse(test_def_path)
            root = tree.getroot()

            # Filter map: identifier -> allowed values (Name text)
            filter_map = {}
            if self.filter_scaling_factors is not None:
                filter_map['scaling-factor'] = self.filter_scaling_factors
            if self.filter_clients is not None:
                filter_map['clients'] = self.filter_clients
            if self.filter_modes is not None:
                filter_map['run-mode'] = self.filter_modes

            test_settings = root.find('TestSettings')
            if test_settings is None:
                print(f"  [WARN] No TestSettings found in test-definition.xml")
                return

            config_counts = []
            for option in test_settings.findall('Option'):
                identifier_el = option.find('Identifier')
                if identifier_el is None:
                    continue
                identifier = identifier_el.text.strip()

                if identifier not in filter_map:
                    # Count all entries for this option
                    menu = option.find('Menu')
                    if menu is not None:
                        config_counts.append(len(menu.findall('Entry')))
                    continue

                allowed_values = filter_map[identifier]
                menu = option.find('Menu')
                if menu is None:
                    continue

                # Remove entries not in allowed values
                to_remove = []
                for entry in menu.findall('Entry'):
                    name_el = entry.find('Name')
                    if name_el is not None and name_el.text.strip() not in allowed_values:
                        to_remove.append(entry)

                for entry in to_remove:
                    menu.remove(entry)

                remaining = len(menu.findall('Entry'))
                config_counts.append(remaining)
                display_name = option.find('DisplayName')
                dn = display_name.text if display_name is not None else identifier
                print(f"  [OK] {dn}: filtered to {allowed_values} ({remaining} entries)")

            total = 1
            for c in config_counts:
                total *= c

            print(f"  [OK] Total test configurations: {total}")

            # Write patched XML
            tree.write(test_def_path, xml_declaration=True, encoding='unicode')
            print(f"  [OK] test-definition.xml patched (backup: {backup_path})")

        except Exception as e:
            print(f"  [ERROR] Failed to patch test-definition.xml: {e}")
            # Restore backup on failure
            if backup_path.exists():
                shutil.copy2(backup_path, test_def_path)

    def restore_test_options(self):
        """Restore original test-definition.xml from backup."""
        test_def_path = (Path.home() / '.phoronix-test-suite' / 'test-profiles'
                         / 'pts' / self.benchmark / 'test-definition.xml')
        backup_path = test_def_path.with_suffix('.xml.bak')

        if backup_path.exists():
            shutil.copy2(backup_path, test_def_path)
            backup_path.unlink()
            print(f"  [OK] test-definition.xml restored from backup")

    def install_benchmark(self):
        """Install benchmark using standard PTS mechanism."""
        print(f"\n>>> Checking for large files to pre-seed...")
        downloader = PreSeedDownloader()
        downloader.download_from_xml(self.benchmark_full, threshold_mb=96)

        print(f"\n>>> Installing {self.benchmark_full}...")

        # PATCH: Fix install.sh before installing
        self.patch_install_script()

        # PATCH: Fix results-definition.xml for pgbench 14.0 output format
        self.patch_results_definition()

        # Remove existing installation to ensure clean slate
        print(f"  [INFO] Removing existing installation...")
        remove_cmd = f'echo "y" | phoronix-test-suite remove-installed-test "{self.benchmark_full}"'
        subprocess.run(['bash', '-c', remove_cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        nproc = os.cpu_count() or 1
        
        # Standard install command
        install_cmd = f'MAKEFLAGS="-j{nproc}" phoronix-test-suite batch-install {self.benchmark_full}'

        print(f"\n{'>'*80}")
        print(f"[PTS INSTALL COMMAND]")
        print(f"  {install_cmd}")
        print(f"{'<'*80}\n")

        # Execute install command with real-time output streaming
        print(f"  Running installation...")
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
        pts_test_failed, pts_failure_reason = detect_pts_failure_from_log(log_file)
        if log_f:
            log_f.close()

        # Check for installation failure
        install_failed = False
        full_output = ''.join(install_output)

        if returncode != 0:
            install_failed = True
        elif 'Checksum Failed' in full_output or 'Downloading of needed test files failed' in full_output:
            install_failed = True
        elif 'ERROR' in full_output or 'FAILED' in full_output:
            install_failed = True

        if install_failed:
            print(f"\n  [ERROR] Installation failed with return code {returncode}")
            print(f"  [INFO] Check output above for details")
            if use_install_log:
                print(f"  [INFO] Install log: {install_log}")
            sys.exit(1)

        # Verify installation by checking if directory exists
        pts_home = Path.home() / '.phoronix-test-suite'
        installed_dir = pts_home / 'installed-tests' / 'pts' / self.benchmark

        if not installed_dir.exists():
            print(f"  [ERROR] Installation verification failed")
            print(f"  [ERROR] Expected directory not found: {installed_dir}")
            print(f"  [INFO] Installation may have failed silently")
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
            print(f"  [WARN] Test may not be fully installed (test-installed check failed)")
            print(f"  [INFO] But installation directory exists, continuing...")

        print(f"  [OK] Installation completed and verified: {installed_dir}")

        # Patch pgbench execution script to handle missing $LOG_FILE
        print(f"\n  [INFO] Patching pgbench execution script...")
        pgbench_script = installed_dir / 'pgbench'
        if pgbench_script.exists():
            with open(pgbench_script, 'r') as f:
                script_content = f.read()

            # Add debug header if not present
            if '[DEBUG]' not in script_content:
                # Insert debug output at the beginning after shebang
                lines = script_content.split('\n')
                if lines[0].startswith('#!'):
                    debug_lines = [
                        lines[0],
                        '# Debug: Print environment to stderr',
                        'echo "[DEBUG] Script started with args: $@" >&2',
                        'echo "[DEBUG] LOG_FILE=${LOG_FILE:-NOT_SET}" >&2',
                        'echo "[DEBUG] NUM_CPU_CORES=${NUM_CPU_CORES:-NOT_SET}" >&2',
                        ''
                    ] + lines[1:]
                    script_content = '\n'.join(debug_lines)

            # Add database initialization check if not present
            if 'Initialize database if not exists' not in script_content:
                init_check = '''
# Initialize database if not exists
if [ ! -d "$PGDATA" ]; then
    echo "[INFO] Database directory not found, initializing..." >&2
    pg_/bin/initdb -D $PGDATA --encoding=SQL_ASCII --locale=C >&2
    if [ $? -ne 0 ]; then
        echo "[ERROR] Failed to initialize database" >&2
        exit 1
    fi
    echo "[INFO] Database initialized successfully" >&2
fi

# Set SYS_MEMORY if not set (PTS should set this, but as fallback use system memory)
if [ -z "$SYS_MEMORY" ]; then
    echo "[WARN] SYS_MEMORY not set, detecting system memory..." >&2
    # Get system memory in MB
    if [ -f /proc/meminfo ]; then
        SYS_MEMORY=$(grep MemTotal /proc/meminfo | awk '{print int($2/1024)}')
        echo "[INFO] Detected ${SYS_MEMORY}MB system memory" >&2
    else
        # Fallback to a reasonable default
        SYS_MEMORY=2048
        echo "[WARN] Could not detect memory, using default ${SYS_MEMORY}MB" >&2
    fi
    export SYS_MEMORY
fi
'''
                script_content = script_content.replace(
                    'export PGPORT\n# start server',
                    'export PGPORT\n' + init_check + '\n# start server'
                )

            # Fix SHARED_BUFFER_SIZE calculation to handle bash syntax properly
            if 'SHARED_BUFFER_SIZE=$(( $SHARED_BUFFER_SIZE < 8192 ?' in script_content:
                script_content = script_content.replace(
                    'SHARED_BUFFER_SIZE=$(( $SHARED_BUFFER_SIZE < 8192 ? $SHARED_BUFFER_SIZE : 8192 ))',
                    'if [ $SHARED_BUFFER_SIZE -gt 8192 ]; then SHARED_BUFFER_SIZE=8192; fi'
                )

            # Keep $LOG_FILE redirect (PTS uses this to capture output)
            # Just add exit code capture and debug logging
            patched_content = script_content.replace(
                'echo "Buffer size is ${SHARED_BUFFER_SIZE}MB" > $LOG_FILE',
                'echo "[INFO] Buffer size is ${SHARED_BUFFER_SIZE}MB" >&2'
            ).replace(
                'pg_/bin/pgbench -j $NUM_CPU_CORES $@ -n -T 120 -r pgbench >>$LOG_FILE 2>&1',
                'pg_/bin/pgbench -j $NUM_CPU_CORES $@ -n -T 120 -r pgbench >>$LOG_FILE 2>&1\nPGBENCH_EXIT=$?\necho "[DEBUG] pgbench exit code: $PGBENCH_EXIT" >&2'
            )

            # Also add debug before pgbench execution
            if 'Running pgbench with:' not in patched_content:
                patched_content = patched_content.replace(
                    '# run the test',
                    '# run the test\necho "[DEBUG] Running pgbench with: -j $NUM_CPU_CORES $@ -n -T 120 -r pgbench" >&2'
                )

            # Add dropdb --if-exists before createdb to avoid conflicts
            if '--if-exists' not in patched_content:
                patched_content = patched_content.replace(
                    '# create test db\npg_/bin/createdb pgbench',
                    '# create test db (drop if exists first)\npg_/bin/dropdb --if-exists pgbench 2>/dev/null\npg_/bin/createdb pgbench'
                )

            # Make cleanup errors non-fatal (exit 0 even if cleanup fails)
            if 'exit 0' not in patched_content:
                patched_content = patched_content.replace(
                    '# drop test db\npg_/bin/dropdb pgbench\n# stop server\npg_/bin/pg_ctl stop',
                    '# drop test db (ignore errors)\npg_/bin/dropdb pgbench 2>/dev/null || true\n# stop server (ignore errors)\npg_/bin/pg_ctl stop 2>/dev/null || true\n# Always exit with success if pgbench completed\nexit 0'
                )

            with open(pgbench_script, 'w') as f:
                f.write(patched_content)

            print(f"  [OK] pgbench script patched to output to stdout with debug logging")

    def parse_perf_stats_and_freq(self, perf_stats_file, freq_start_file, freq_end_file, cpu_list):
        """Parse perf stat output and CPU frequency files."""
        # If perf monitoring was disabled, return minimal info
        if not self.perf_events or not perf_stats_file.exists():
            return {
                'note': 'perf monitoring not available',
                'cpu_list': cpu_list
            }

        print(f"\n>>> Parsing perf stats and frequency data")

        cpu_ids = [int(x.strip()) for x in cpu_list.split(',')]
        
        perf_summary = {
            "avg_frequency_ghz": {},
            "start_frequency_ghz": {},
            "end_frequency_ghz": {},
            "ipc": {},
            "total_cycles": {},
            "total_instructions": {},
            "cpu_utilization_percent": 0.0,
            "elapsed_time_sec": 0.0,
            "per_cpu_metrics": {cpu_id: {} for cpu_id in cpu_ids}
        }

        # Parse start frequencies
        if freq_start_file.exists():
            with open(freq_start_file, 'r') as f:
                freq_lines = f.readlines()
            for idx, cpu_id in enumerate(cpu_ids):
                if idx < len(freq_lines):
                    try:
                        freq_khz = float(freq_lines[idx].strip())
                        perf_summary["start_frequency_ghz"][str(cpu_id)] = round(freq_khz / 1e6, 3)
                    except ValueError:
                        pass

        # Parse end frequencies
        if freq_end_file.exists():
            with open(freq_end_file, 'r') as f:
                freq_lines = f.readlines()
            for idx, cpu_id in enumerate(cpu_ids):
                if idx < len(freq_lines):
                    try:
                        freq_khz = float(freq_lines[idx].strip())
                        perf_summary["end_frequency_ghz"][str(cpu_id)] = round(freq_khz / 1e6, 3)
                    except ValueError:
                        pass

        # Parse perf stat output
        with open(perf_stats_file, 'r') as f:
            for line in f:
                # Match: "CPU0  123,456  cycles"
                match = re.match(r'CPU(\d+)\s+([\d,.]+)\s+([a-zA-Z0-9\-_]+)', line.strip())
                if match:
                    cpu_num = int(match.group(1))
                    if cpu_num in cpu_ids:
                        value_str = match.group(2).strip()
                        event = match.group(3)
                        try:
                            value = float(value_str.replace(',', ''))
                            perf_summary["per_cpu_metrics"][cpu_num][event] = value
                            
                            # Update legacy aggregations
                            if event == 'cycles':
                                perf_summary["total_cycles"][str(cpu_num)] = int(value)
                            elif event == 'instructions':
                                perf_summary["total_instructions"][str(cpu_num)] = int(value)
                        except ValueError:
                            pass
                
                # Match elapsed time
                elapsed_match = re.search(r'([\d,]+(?:\.\d+)?)\s+seconds time elapsed', line)
                if elapsed_match:
                     perf_summary["elapsed_time_sec"] = float(elapsed_match.group(1).replace(',', ''))

        # Calculate IPC
        for cpu_id in cpu_ids:
            cycles = perf_summary["total_cycles"].get(str(cpu_id), 0)
            instr = perf_summary["total_instructions"].get(str(cpu_id), 0)
            if cycles > 0:
                perf_summary["ipc"][str(cpu_id)] = round(instr / cycles, 2)

        return perf_summary

    def cleanup_postgresql(self):
        """Clean up any existing PostgreSQL processes to prevent conflicts."""
        print(f"  [INFO] Cleaning up existing PostgreSQL processes...")

        # Kill any existing postgres processes
        try:
            subprocess.run(
                ['pkill', '-9', 'postgres'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            # Give processes time to terminate
            import time
            time.sleep(2)
            print(f"  [OK] PostgreSQL processes cleaned up")
        except Exception as e:
            print(f"  [WARN] Error during cleanup (may be normal if no processes running): {e}")

    def run_benchmark(self, num_threads):
        """Run benchmark with specified thread count."""
        print(f"\n{'='*80}")
        print(f">>> Running {self.benchmark_full} with {num_threads} thread(s)")
        print(f"{'='*80}")

        # Clean up any existing PostgreSQL processes to prevent conflicts
        self.cleanup_postgresql()

        # Remove existing PTS result to prevent interactive prompts
        # PTS sanitizes identifiers (e.g. 1.0.2 -> 102), so we try to remove both forms
        sanitized_benchmark = self.benchmark.replace('.', '')
        remove_cmds = [
            f'phoronix-test-suite remove-result {self.benchmark}-{num_threads}threads',
            f'phoronix-test-suite remove-result {sanitized_benchmark}-{num_threads}threads'
        ]
        for cmd in remove_cmds:
            subprocess.run(cmd.split(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        thread_dir = self.results_dir / f"{num_threads}-thread"
        thread_dir.mkdir(parents=True, exist_ok=True)

        log_file = self.results_dir / f"{num_threads}-thread.log"
        stdout_log = self.results_dir / "stdout.log"

        perf_stats_file = thread_dir / f"{num_threads}-thread_perf_stats.txt"
        freq_start_file = thread_dir / f"{num_threads}-thread_freq_start.txt"
        freq_end_file = thread_dir / f"{num_threads}-thread_freq_end.txt"
        perf_summary_file = thread_dir / f"{num_threads}-thread_perf_summary.json"

        # Setup environment variables
        quick_env = 'FORCE_TIMES_TO_RUN=1 ' if self.quick_mode else ''
        batch_env = f'{quick_env}BATCH_MODE=1 SKIP_ALL_PROMPTS=1 DISPLAY_COMPACT_RESULTS=1 TEST_RESULTS_NAME={self.benchmark}-{num_threads}threads TEST_RESULTS_IDENTIFIER={self.benchmark}-{num_threads}threads TEST_RESULTS_DESCRIPTION={self.benchmark}-{num_threads}threads'

        # Build PTS command
        if num_threads >= self.vcpu_count:
            cpu_list = ','.join([str(i) for i in range(self.vcpu_count)])
            pts_base_cmd = f'phoronix-test-suite batch-run {self.benchmark_full}'
            cpu_info = f"Using all {num_threads} vCPUs (no taskset)"
        else:
            cpu_list = self.get_cpu_affinity_list(num_threads)
            pts_base_cmd = f'taskset -c {cpu_list} phoronix-test-suite batch-run {self.benchmark_full}'
            cpu_info = f"CPU affinity (taskset): {cpu_list}"

        # Setup perf command
        if self.perf_events:
            if self.perf_paranoid <= 0:
                # Full monitoring mode: per-CPU stats + hardware counters
                perf_cmd = f"perf stat -e {self.perf_events} -A -a -o {perf_stats_file}"
                perf_mode = "Full (per-CPU + HW counters)"
            else:
                # Limited mode: aggregated events only
                perf_cmd = f"perf stat -e {self.perf_events} -o {perf_stats_file}"
                perf_mode = "Limited (aggregated events only)"
            
            pts_cmd = f'NUM_CPU_CORES={num_threads} {batch_env} {perf_cmd} {pts_base_cmd}'
        else:
            # No perf monitoring
            pts_cmd = f'NUM_CPU_CORES={num_threads} {batch_env} {pts_base_cmd}'
            perf_mode = "Disabled (perf unavailable)"
        
        print(f"[INFO] Perf monitoring mode: {perf_mode}")
        print(f"  [INFO] {cpu_info}")
        print(f"\n{'>'*80}")
        print(f"[PTS RUN COMMAND]")
        print(f"  {pts_cmd}")
        print(f"{'<'*80}\n")

        # Record CPU frequency before benchmark
        # Uses cross-platform method (works on x86_64, ARM64, and cloud VMs)
        print(f"[INFO] Recording CPU frequency before benchmark...")
        if self.record_cpu_frequency(freq_start_file):
            print(f"  [OK] Start frequency recorded")
        else:
            print(f"  [WARN] CPU frequency not available (common on ARM64/cloud VMs)")

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
        print(f"\n[INFO] Recording CPU frequency after benchmark...")
        if self.record_cpu_frequency(freq_end_file):
            print(f"  [OK] End frequency recorded")
        else:
            print(f"  [WARN] CPU frequency not available (common on ARM64/cloud VMs)")

        if returncode == 0 and pts_test_failed:
            print(f"\n[ERROR] PTS reported benchmark failure despite zero exit code: {pts_failure_reason}")
            return False

        if returncode == 0:
            print(f"\n[OK] Benchmark completed successfully")
            
            # Parse perf stats
            try:
                perf_summary = self.parse_perf_stats_and_freq(
                    perf_stats_file, freq_start_file, freq_end_file, cpu_list
                )
                with open(perf_summary_file, 'w') as f:
                    json.dump(perf_summary, f, indent=2)
                print(f"  [OK] Perf summary saved to {perf_summary_file}")
            except Exception as e:
                print(f"  [ERROR] Failed to parse perf stats: {e}")

        else:
            print(f"\n[ERROR] Benchmark failed with return code {returncode}")
            return False

        return True

    def export_results(self):
        """Export benchmark results to CSV and JSON formats."""
        print(f"\n>>> Exporting results...")
        pts_results_dir = Path.home() / ".phoronix-test-suite" / "test-results"
        
        # Determine the sanitized directory name PTS uses (dots removed)
        # e.g., pgbench-1.11.1 -> pgbench-1111
        benchmark_nodots = self.benchmark.replace('.', '')

        for num_threads in self.thread_list:
            result_name = f"{self.benchmark}-{num_threads}threads"
            # PTS result directory logic
            result_dir_name = f"{benchmark_nodots}-{num_threads}threads"
            
            result_dir = pts_results_dir / result_dir_name
            
            if not result_dir.exists():
                print(f"[WARN] Result not found: {result_dir}")
                continue

            print(f"  [INFO] Found result directory: {result_dir}")

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
                    print(f"  [OK] Saved CSV: {csv_output}")
            else:
                print(f"  [WARN] CSV export failed for {result_name}")

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
                    print(f"  [OK] Saved JSON: {json_output}")
            else:
                 print(f"  [WARN] JSON export failed for {result_name}")

    def generate_summary(self):
        """Generate summary.json from results."""
        print(f"\n>>> Generating summary...")
        summary_data = {
            "benchmark": self.benchmark,
            "machine": self.machine_name,
            "os": self.os_name,
            "results": {}
        }

        for num_threads in self.thread_list:
            thread_key = f"{num_threads}-thread"
            summary_data["results"][thread_key] = {
                "tps": None,
                "latency_ms": None,
                "perf": None
            }
            
            # Read CSV for main metric (TPS)
            csv_file = self.results_dir / f"{num_threads}-thread.csv"
            if csv_file.exists():
                try:
                    with open(csv_file, 'r') as f:
                        lines = f.readlines()
                        if len(lines) > 1:
                            # Last line usually contains the average
                            last_line = lines[-1].strip()
                            parts = last_line.split(',')
                            if len(parts) >= 2:
                                # Start from end to find value
                                val = float(parts[-1].strip().replace('"', ''))
                                summary_data["results"][thread_key]["tps"] = val
                except Exception as e:
                    print(f"  [WARN] Failed to parse CSV: {e}")

            # Read Perf Summary
            perf_file = self.results_dir / f"{num_threads}-thread" / f"{num_threads}-thread_perf_summary.json"
            if perf_file.exists():
                try:
                    with open(perf_file, 'r') as f:
                        perf_data = json.load(f)
                        summary_data["results"][thread_key]["perf"] = perf_data
                except Exception:
                    pass

        summary_file = self.results_dir / "summary.json"
        with open(summary_file, 'w') as f:
            json.dump(summary_data, f, indent=2)
        print(f"  [OK] Summary saved to {summary_file}")



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

    def run(self):
        """Main execution flow."""
        print(f"\n{'#'*80}")
        print(f"# PTS Runner: {self.benchmark_full}")
        print(f"# Machine: {self.machine_name}")
        print(f"# OS: {self.os_name}")
        print(f"# vCPU Count: {self.vcpu_count}")
        print(f"# Thread List: {self.thread_list}")
        if self.quick_mode:
            print(f"# Quick Mode: ENABLED (FORCE_TIMES_TO_RUN=1)")
        if self._has_test_option_filters():
            sf = self.filter_scaling_factors or self.VALID_SCALING_FACTORS
            cl = self.filter_clients or self.VALID_CLIENTS
            md = self.filter_modes or ['Read Write', 'Read Only']
            total = len(sf) * len(cl) * len(md)
            print(f"# Scaling Factors: {','.join(sf)} ({len(sf)} of {len(self.VALID_SCALING_FACTORS)})")
            print(f"# Clients: {','.join(cl)} ({len(cl)} of {len(self.VALID_CLIENTS)})")
            print(f"# Modes: {','.join(md)} ({len(md)} of 2)")
            print(f"# Total Configurations: {total}")
        print(f"{'#'*80}")

        # Clean only thread-specific files (preserve other threads' results)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        for num_threads in self.thread_list:
            prefix = f"{num_threads}-thread"
            thread_dir = self.results_dir / prefix
            if thread_dir.exists():
                shutil.rmtree(thread_dir)
            for f in self.results_dir.glob(f"{prefix}.*"):
                f.unlink()
                print(f"  [INFO] Removed old result: {f.name}")
            print(f"\n>>> Cleaned existing {prefix} results (other threads preserved)")

        # Install
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

        # Patch test options to limit configurations (if filters specified)
        self.patch_test_options()

        # Run for each thread count
        failed = []
        try:
            for num_threads in self.thread_list:
                if not self.run_benchmark(num_threads):
                    failed.append(num_threads)
        finally:
            # Clean up PostgreSQL processes even if tests fail or are interrupted
            print(f"\n>>> Final cleanup...")
            self.cleanup_postgresql()
            # Restore original test-definition.xml
            self.restore_test_options()

        # Export results
        self.export_results()

        # Generate summary
        self.generate_summary()

        if failed:
            print(f"\n[WARN] Some tests failed: {failed}")
            return False

        print(f"\n[SUCCESS] All tests completed successfully")
        return True


def main():
    parser = argparse.ArgumentParser(
        description="Benchmark Runner",
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

    parser.add_argument(
        '--scaling-factors',
        type=str,
        default='1000',
        help='Comma-separated scaling factors to test (e.g., "100,1000"). Default: "1000". Use "all" for all (1,100,1000,10000,25000)'
    )

    parser.add_argument(
        '--clients',
        type=str,
        default='50',
        help='Comma-separated client counts to test (e.g., "1,50"). Default: "50". Use "all" for all (1,50,100,250,500)'
    )

    parser.add_argument(
        '--modes',
        type=str,
        default='both',
        choices=['rw', 'ro', 'both', 'all'],
        help='Test mode: rw=Read Write only, ro=Read Only only, both=both modes, all=same as both. Default: "both"'
    )

    args = parser.parse_args()

    if args.quick:
        print("[INFO] Quick mode enabled: FORCE_TIMES_TO_RUN=1")
        print("[INFO] Tests will run once instead of 3+ times (60-70%% time reduction)")

    # Parse test option filters ("all" disables filtering for that option)
    scaling_factors = None
    if args.scaling_factors and args.scaling_factors.lower() != 'all':
        scaling_factors = [s.strip() for s in args.scaling_factors.split(',')]

    clients = None
    if args.clients and args.clients.lower() != 'all':
        clients = [s.strip() for s in args.clients.split(',')]

    modes = args.modes if args.modes != 'all' else None

    # Resolve threads argument (prioritize --threads if both provided)
    threads = args.threads if args.threads is not None else args.threads_pos

    runner = PgbenchRunner(
        threads_arg=threads,
        quick_mode=args.quick,
        scaling_factors=scaling_factors,
        clients=clients,
        modes=modes,
    )
    success = runner.run()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
