#!/usr/bin/env python3
"""
PTS Runner for pgbench-1.17.0

System Dependencies (from openbenchmarking.org / phoronix-test-suite info):
- Software Dependencies:
  * bc, libicuuc.so, unicode/utf.h, numactl
- External Dependencies:
  * build-utilities, bc, bison, flex
- Environment Size: 1500 MB
- Test Type: System
- Supported Platforms: Linux, MacOSX, BSD, Solaris
- PostgreSQL Version: 18.1
- Times to Run: 3

Test Characteristics:
- Multi-threaded: Yes (pgbench worker threads scale with cores)
- THFix_in_compile: false
- THChange_at_runtime: true

Test Options:
- Scaling Factor: 1, 100, 1000, 10000, 25000
- Clients: 1, 50, 100, 250, 500, 800, 1000, 5000
- Mode: Read Write, Read Only

Results Metrics:
- Throughput: tps = #_RESULT_# (excluding connections establishing) [TPS, higher is better]
- Latency:    latency average = #_RESULT_# ms [ms, lower is better]
"""

import argparse
import atexit
import json
import os
import re
import shutil
import signal
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

    def get_remote_file_size(self, url):
        """Get remote file size in bytes using curl. Returns -1 if unavailable."""
        try:
            result = subprocess.run(
                ['curl', '-s', '-I', '-L', url],
                capture_output=True, text=True
            )
            if result.returncode != 0:
                return -1
            for line in result.stdout.splitlines():
                if line.lower().startswith('content-length:'):
                    try:
                        return int(line.split(':')[1].strip())
                    except ValueError:
                        pass
        except Exception as e:
            print(f"  [WARN] Error checking size: {e}")
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
    def download_from_xml(self, benchmark_name, threshold_mb=96):
        """Parse downloads.xml for the benchmark and download large files."""
        if not self.aria2_available:
            return False

        profile_path = (Path.home() / ".phoronix-test-suite" / "test-profiles"
                        / benchmark_name / "downloads.xml")
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

                urls = [u.strip() for u in url_node.text.split(',')]
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
    # Valid values must match test-definition.xml of pts/pgbench-1.17.0
    VALID_SCALING_FACTORS = ['1', '100', '1000', '10000', '25000']
    VALID_CLIENTS = ['1', '50', '100', '250', '500', '800', '1000', '5000']
    VALID_MODES = {'rw': ['Read Write'], 'ro': ['Read Only'], 'both': ['Read Write', 'Read Only']}

    def __init__(self, threads_arg=None, quick_mode=False,
                 scaling_factors=None, clients=None, modes=None):
        self.benchmark = "pgbench-1.17.0"
        self.benchmark_full = f"pts/{self.benchmark}"
        self.test_category = "Database"
        self.test_category_dir = self.test_category.replace(" ", "_")

        # System info
        self.vcpu_count = os.cpu_count() or 1
        self.machine_name = os.environ.get('MACHINE_NAME', os.uname().nodename)
        self.os_name = self.get_os_name()

        # Thread list setup
        if threads_arg is None:
            # 4-point scaling: [nproc/4, nproc/2, nproc*3/4, nproc]
            n_4 = self.vcpu_count // 4
            self.thread_list = [n_4, n_4 * 2, n_4 * 3, self.vcpu_count]
            self.thread_list = sorted(list(set([t for t in self.thread_list if t > 0])))
        else:
            n = min(threads_arg, self.vcpu_count)
            self.thread_list = [n]

        # Results directory
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

        # Detect environment for logging
        self.is_wsl_env = self.is_wsl()
        if self.is_wsl_env:
            print("  [INFO] Running on WSL environment")

        # CRITICAL: Setup perf permissions BEFORE testing perf availability
        self.perf_paranoid = self.check_and_setup_perf_permissions()

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

    # -------------------------------------------------------------------------
    # Cleanup handlers
    # -------------------------------------------------------------------------

    def _cleanup_handler(self, signum=None, _frame=None):
        """Handler for signals and atexit to ensure PostgreSQL cleanup."""
        if signum is not None:
            print(f"\n[SIGNAL] Received signal {signum}, cleaning up...")
        else:
            print("\n[EXIT] Script terminating, cleaning up...")
        self.cleanup_postgresql()
        if signum is not None:
            sys.exit(1)

    def _setup_cleanup_handlers(self):
        """Register signal handlers and atexit for cleanup."""
        atexit.register(lambda: self._cleanup_handler())
        signal.signal(signal.SIGINT, self._cleanup_handler)
        signal.signal(signal.SIGTERM, self._cleanup_handler)

    # -------------------------------------------------------------------------
    # Argument validation
    # -------------------------------------------------------------------------

    def _validate_filter(self, values, valid_values, name):
        """Validate filter values against allowed list. Returns None if no filter."""
        if values is None:
            return None
        invalid = [v for v in values if v not in valid_values]
        if invalid:
            print(f"  [ERROR] Invalid {name}: {invalid}. Valid: {valid_values}")
            sys.exit(1)
        return values

    def _has_test_option_filters(self):
        return (self.filter_scaling_factors is not None
                or self.filter_clients is not None
                or self.filter_modes is not None)

    # -------------------------------------------------------------------------
    # Standard methods (from CODE_TEMPLATE.md)
    # -------------------------------------------------------------------------

    def get_os_name(self):
        """Get OS name and version formatted as <Distro>_<Version>."""
        try:
            result = subprocess.run(
                "lsb_release -d -s".split(), capture_output=True, text=True)
            if result.returncode == 0:
                parts = result.stdout.strip().split()
                if len(parts) >= 2:
                    return f"{parts[0]}_{parts[1].replace('.', '_')}"
        except Exception:
            pass
        try:
            with open('/etc/os-release', 'r') as f:
                info = {}
                for line in f:
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

    def get_cpu_affinity_list(self, n):
        """Generate CPU affinity list for HyperThreading optimization."""
        half = self.vcpu_count // 2
        if n <= half:
            cpu_list = [str(i * 2) for i in range(n)]
        else:
            cpu_list = [str(i * 2) for i in range(half)]
            cpu_list.extend([str(i * 2 + 1) for i in range(n - half)])
        return ','.join(cpu_list)

    def get_cpu_frequencies(self):
        """Get current CPU frequencies for all CPUs (cross-platform)."""
        frequencies = []
        # Method 1: /proc/cpuinfo (x86_64)
        try:
            result = subprocess.run(
                ['bash', '-c', 'grep "cpu MHz" /proc/cpuinfo'],
                capture_output=True, text=True)
            if result.returncode == 0 and result.stdout.strip():
                for line in result.stdout.strip().split('\n'):
                    parts = line.split(':')
                    if len(parts) >= 2:
                        frequencies.append(int(float(parts[1].strip()) * 1000))
                if frequencies:
                    return frequencies
        except Exception:
            pass
        # Method 2: /sys (ARM64 and some x86)
        try:
            freq_files = sorted(Path('/sys/devices/system/cpu').glob(
                'cpu[0-9]*/cpufreq/scaling_cur_freq'))
            if not freq_files:
                freq_files = sorted(Path('/sys/devices/system/cpu').glob(
                    'cpu[0-9]*/cpufreq/cpuinfo_cur_freq'))
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
        # Method 3: lscpu (fallback)
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
            try:
                open(output_file, 'w').close()
            except Exception:
                pass
            return False

    def get_perf_events(self):
        """
        Determine available perf events by testing actual command execution.
        Tests: HW+SW → SW-only → None
        """
        perf_path = shutil.which("perf")
        if not perf_path:
            print("  [INFO] perf command not found in PATH")
            return None

        hw_events = "cycles,instructions,cpu-clock,task-clock,context-switches,cpu-migrations"
        try:
            result = subprocess.run(
                ['bash', '-c', f"{perf_path} stat -e {hw_events} sleep 0.01 2>&1"],
                capture_output=True, text=True, timeout=3)
            output = result.stdout + result.stderr
            if result.returncode == 0 and '<not supported>' not in output:
                print(f"  [OK] Hardware PMU available: {hw_events}")
                return hw_events

            sw_events = "cpu-clock,task-clock,context-switches,cpu-migrations"
            result_sw = subprocess.run(
                ['bash', '-c', f"{perf_path} stat -e {sw_events} sleep 0.01 2>&1"],
                capture_output=True, text=True, timeout=3)
            if result_sw.returncode == 0:
                print(f"  [INFO] Hardware PMU not available. Using software events: {sw_events}")
                return sw_events
        except subprocess.TimeoutExpired:
            print("  [WARN] perf test timed out")
        except Exception as e:
            print(f"  [DEBUG] perf test execution failed: {e}")

        print("  [INFO] perf command exists but is not functional")
        return None

    def check_and_setup_perf_permissions(self):
        """Check perf_event_paranoid setting and adjust if needed."""
        print(f"\n{'='*80}")
        print(">>> Checking perf_event_paranoid setting")
        print(f"{'='*80}")
        try:
            result = subprocess.run(
                ['cat', '/proc/sys/kernel/perf_event_paranoid'],
                capture_output=True, text=True, check=True)
            current_value = int(result.stdout.strip())
            print(f"  [INFO] Current perf_event_paranoid: {current_value}")

            if current_value >= 1:
                print(f"  [WARN] perf_event_paranoid={current_value} is too restrictive")
                print("  [INFO] Attempting to adjust to 0...")
                result = subprocess.run(
                    ['sudo', 'sysctl', '-w', 'kernel.perf_event_paranoid=0'],
                    capture_output=True, text=True)
                if result.returncode == 0:
                    print("  [OK] perf_event_paranoid adjusted to 0")
                    return 0
                else:
                    print("  [ERROR] Failed to adjust (sudo required)")
                    print("  [WARN] Running in LIMITED mode")
                    return current_value
            else:
                print(f"  [OK] perf_event_paranoid={current_value} is acceptable")
                return current_value
        except Exception as e:
            print(f"  [ERROR] Could not check perf_event_paranoid: {e}")
            return 2

    def parse_perf_stats_and_freq(self, perf_stats_file, freq_start_file, freq_end_file, cpu_list):
        """Parse perf stat output and CPU frequency files."""
        if not self.perf_events or not perf_stats_file.exists():
            return {'note': 'perf monitoring not available', 'cpu_list': cpu_list}

        cpu_ids = [int(c.strip()) for c in cpu_list.split(',')]
        per_cpu_metrics = {cpu_id: {} for cpu_id in cpu_ids}
        start_freq_ghz = {}
        end_freq_ghz = {}

        # Parse start frequencies
        if freq_start_file.exists():
            with open(freq_start_file, 'r') as f:
                freq_lines = f.readlines()
            for idx, cpu_id in enumerate(cpu_ids):
                if idx < len(freq_lines):
                    try:
                        start_freq_ghz[str(cpu_id)] = round(float(freq_lines[idx].strip()) / 1e6, 3)
                    except ValueError:
                        pass

        # Parse end frequencies
        if freq_end_file.exists():
            with open(freq_end_file, 'r') as f:
                freq_lines = f.readlines()
            for idx, cpu_id in enumerate(cpu_ids):
                if idx < len(freq_lines):
                    try:
                        end_freq_ghz[str(cpu_id)] = round(float(freq_lines[idx].strip()) / 1e6, 3)
                    except ValueError:
                        pass

        # Parse perf stat output
        try:
            with open(perf_stats_file, 'r') as f:
                for line in f:
                    match = re.match(r'CPU(\d+)\s+([\d,.<>a-zA-Z\s]+)\s+([a-zA-Z0-9\-_]+)', line)
                    if match:
                        cpu_num = int(match.group(1))
                        value_str = match.group(2).strip()
                        event = match.group(3)
                        if cpu_num in per_cpu_metrics and '<not supported>' not in value_str:
                            try:
                                value = float(value_str.split()[0].replace(',', ''))
                                per_cpu_metrics[cpu_num][event] = value
                            except ValueError:
                                continue
        except FileNotFoundError:
            print(f"  [INFO] Perf stats not found: {perf_stats_file} (likely disabled or missing)")

        return {
            'cpu_list': cpu_list,
            'start_frequency_ghz': start_freq_ghz,
            'end_frequency_ghz': end_freq_ghz,
            'per_cpu_metrics': {str(k): v for k, v in per_cpu_metrics.items()}
        }

    def ensure_upload_disabled(self):
        """Ensure PTS results upload is disabled in user-config.xml."""
        config_path = Path.home() / ".phoronix-test-suite" / "user-config.xml"
        if not config_path.exists():
            return
        try:
            with open(config_path, 'r') as f:
                content = f.read()
            if '<UploadResults>TRUE</UploadResults>' in content:
                print("  [WARN] UploadResults is TRUE in user-config.xml. Disabling...")
                content = content.replace(
                    '<UploadResults>TRUE</UploadResults>',
                    '<UploadResults>FALSE</UploadResults>')
                with open(config_path, 'w') as f:
                    f.write(content)
                print("  [OK] UploadResults set to FALSE")
        except Exception as e:
            print(f"  [WARN] Failed to check/update user-config.xml: {e}")

    # -------------------------------------------------------------------------
    # PostgreSQL management
    # -------------------------------------------------------------------------

    def cleanup_postgresql(self):
        """Clean up any existing PostgreSQL processes to prevent conflicts."""
        print("  [INFO] Cleaning up existing PostgreSQL processes...")
        try:
            subprocess.run(
                ['pkill', '-9', 'postgres'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            import time
            time.sleep(2)
            print("  [OK] PostgreSQL processes cleaned up")
        except Exception as e:
            print(f"  [WARN] Cleanup error (may be normal if no processes running): {e}")

    # -------------------------------------------------------------------------
    # Test option patching
    # -------------------------------------------------------------------------

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
        print("\n>>> Patching test-definition.xml to filter test configurations...")

        try:
            shutil.copy2(test_def_path, backup_path)
            tree = ET.parse(test_def_path)
            root = tree.getroot()

            filter_map = {}
            if self.filter_scaling_factors is not None:
                filter_map['scaling-factor'] = self.filter_scaling_factors
            if self.filter_clients is not None:
                filter_map['clients'] = self.filter_clients
            if self.filter_modes is not None:
                filter_map['run-mode'] = self.filter_modes

            test_settings = root.find('TestSettings')
            if test_settings is None:
                print("  [WARN] No TestSettings found in test-definition.xml")
                return

            config_counts = []
            for option in test_settings.findall('Option'):
                identifier_el = option.find('Identifier')
                if identifier_el is None:
                    continue
                identifier = identifier_el.text.strip()

                if identifier not in filter_map:
                    menu = option.find('Menu')
                    if menu is not None:
                        config_counts.append(len(menu.findall('Entry')))
                    continue

                allowed_values = filter_map[identifier]
                menu = option.find('Menu')
                if menu is None:
                    continue

                to_remove = [
                    entry for entry in menu.findall('Entry')
                    if (lambda e: e.find('Name') is not None and
                        e.find('Name').text.strip() not in allowed_values)(entry)
                ]
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

            tree.write(test_def_path, xml_declaration=True, encoding='unicode')
            print(f"  [OK] test-definition.xml patched (backup: {backup_path})")

        except Exception as e:
            print(f"  [ERROR] Failed to patch test-definition.xml: {e}")
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
            print("  [OK] test-definition.xml restored from backup")

    # -------------------------------------------------------------------------
    # Install
    # -------------------------------------------------------------------------

    def install_benchmark(self):
        """Install benchmark using standard PTS mechanism."""
        print("\n>>> Checking for large files to pre-seed...")
        downloader = PreSeedDownloader()
        downloader.download_from_xml(self.benchmark_full, threshold_mb=96)

        print(f"\n>>> Installing {self.benchmark_full}...")
        print("  [INFO] Removing existing installation...")
        remove_cmd = f'echo "y" | phoronix-test-suite remove-installed-test "{self.benchmark_full}"'
        subprocess.run(['bash', '-c', remove_cmd],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        nproc = os.cpu_count() or 1
        install_cmd = f'MAKEFLAGS="-j{nproc}" phoronix-test-suite batch-install {self.benchmark_full}'

        print(f"\n{'>'*80}")
        print("[PTS INSTALL COMMAND]")
        print(f"  {install_cmd}")
        print(f"{'<'*80}\n")

        # Optional install log
        install_log_env = os.environ.get("PTS_INSTALL_LOG", "").strip().lower()
        install_log_path = os.environ.get("PTS_INSTALL_LOG_PATH", "").strip()
        use_install_log = install_log_env in {"1", "true", "yes"} or bool(install_log_path)
        install_log = (Path(install_log_path) if install_log_path
                       else self.results_dir / "install.log")
        self.results_dir.mkdir(parents=True, exist_ok=True)
        log_f = open(install_log, 'w') if use_install_log else None
        if log_f:
            log_f.write(f"[PTS INSTALL COMMAND]\n{install_cmd}\n\n")
            log_f.flush()

        print("  Running installation...")
        process = subprocess.Popen(
            ['bash', '-c', install_cmd],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1)

        install_output = []
        for line in process.stdout:
            print(line, end='')
            if log_f:
                log_f.write(line)
                log_f.flush()
            install_output.append(line)

        process.wait()
        returncode = process.returncode
        # MUST define log_file before detect_pts_failure_from_log
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
        elif 'ERROR' in full_output or 'FAILED' in full_output:
            install_failed = True

        if install_failed:
            print(f"\n  [ERROR] Installation failed with return code {returncode}")
            if pts_failure_reason:
                print(f"  [INFO] Reason: {pts_failure_reason}")
            print("  [INFO] Check output above for details")
            if use_install_log:
                print(f"  [INFO] Install log: {install_log}")
            sys.exit(1)

        # Verify installation
        install_dir = (Path.home() / '.phoronix-test-suite'
                       / 'installed-tests' / 'pts' / self.benchmark)
        if not install_dir.exists():
            print(f"  [ERROR] Installation verification failed: {install_dir} not found")
            sys.exit(1)

        verify_result = subprocess.run(
            ['bash', '-c', f'phoronix-test-suite test-installed {self.benchmark_full}'],
            capture_output=True, text=True)
        if verify_result.returncode != 0:
            print("  [WARN] test-installed check failed, but directory exists — continuing")

        print(f"  [OK] Installation completed and verified: {install_dir}")

    # -------------------------------------------------------------------------
    # Run
    # -------------------------------------------------------------------------

    def run_benchmark(self, num_threads):
        """Run benchmark with specified thread count."""
        print(f"\n{'='*80}")
        print(f">>> Running {self.benchmark_full} with {num_threads} thread(s)")
        print(f"{'='*80}")

        self.cleanup_postgresql()

        # Remove existing PTS result to prevent interactive prompts
        sanitized_benchmark = self.benchmark.replace('.', '')
        for cmd in [
            f'phoronix-test-suite remove-result {self.benchmark}-{num_threads}threads',
            f'phoronix-test-suite remove-result {sanitized_benchmark}-{num_threads}threads'
        ]:
            subprocess.run(['bash', '-c', cmd],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        thread_dir = self.results_dir / f"{num_threads}-thread"
        thread_dir.mkdir(parents=True, exist_ok=True)

        log_file = self.results_dir / f"{num_threads}-thread.log"
        stdout_log = self.results_dir / "stdout.log"
        perf_stats_file = thread_dir / f"{num_threads}-thread_perf_stats.txt"
        freq_start_file = thread_dir / f"{num_threads}-thread_freq_start.txt"
        freq_end_file = thread_dir / f"{num_threads}-thread_freq_end.txt"
        perf_summary_file = thread_dir / f"{num_threads}-thread_perf_summary.json"

        # Build PTS base command
        if num_threads >= self.vcpu_count:
            cpu_list = ','.join([str(i) for i in range(self.vcpu_count)])
            pts_base_cmd = f'phoronix-test-suite batch-run {self.benchmark_full}'
            cpu_info = f"Using all {num_threads} vCPUs (no taskset)"
        else:
            cpu_list = self.get_cpu_affinity_list(num_threads)
            pts_base_cmd = f'taskset -c {cpu_list} phoronix-test-suite batch-run {self.benchmark_full}'
            cpu_info = f"CPU affinity (taskset): {cpu_list}"

        quick_env = 'FORCE_TIMES_TO_RUN=1 ' if self.quick_mode else ''
        batch_env = (f'{quick_env}BATCH_MODE=1 SKIP_ALL_PROMPTS=1 DISPLAY_COMPACT_RESULTS=1 '
                     f'TEST_RESULTS_NAME={self.benchmark}-{num_threads}threads '
                     f'TEST_RESULTS_IDENTIFIER={self.benchmark}-{num_threads}threads '
                     f'TEST_RESULTS_DESCRIPTION={self.benchmark}-{num_threads}threads')

        if self.perf_events:
            if self.perf_paranoid <= 0:
                perf_cmd = f"perf stat -e {self.perf_events} -A -a -o {perf_stats_file}"
                perf_mode = "Full (per-CPU + HW counters)"
            else:
                perf_cmd = f"perf stat -e {self.perf_events} -o {perf_stats_file}"
                perf_mode = "Limited (aggregated events only)"
            pts_cmd = f'NUM_CPU_CORES={num_threads} {batch_env} {perf_cmd} {pts_base_cmd}'
        else:
            pts_cmd = f'NUM_CPU_CORES={num_threads} {batch_env} {pts_base_cmd}'
            perf_mode = "Disabled (perf unavailable)"

        print(f"  [INFO] Perf monitoring mode: {perf_mode}")
        print(f"  [INFO] {cpu_info}")
        print(f"\n{'>'*80}")
        print("[PTS RUN COMMAND]")
        print(f"  {pts_cmd}")
        print(f"{'<'*80}\n")

        print("[INFO] Recording CPU frequency before benchmark...")
        if self.record_cpu_frequency(freq_start_file):
            print("  [OK] Start frequency recorded")
        else:
            print("  [WARN] CPU frequency not available (common on ARM64/cloud VMs)")

        with open(log_file, 'w') as log_f, open(stdout_log, 'a') as stdout_f:
            stdout_f.write(f"\n{'='*80}\n")
            stdout_f.write(f"[PTS BENCHMARK COMMAND - {num_threads} thread(s)]\n")
            stdout_f.write(f"{pts_cmd}\n")
            stdout_f.write(f"{'='*80}\n\n")
            stdout_f.flush()

            process = subprocess.Popen(
                ['bash', '-c', pts_cmd],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1)

            for line in process.stdout:
                print(line, end='')
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
            print(f"\n[ERROR] PTS reported benchmark failure: {pts_failure_reason}")
            return False

        if returncode != 0:
            print(f"\n[ERROR] Benchmark failed with return code {returncode}")
            return False

        print("\n[OK] Benchmark completed successfully")
        try:
            perf_summary = self.parse_perf_stats_and_freq(
                perf_stats_file, freq_start_file, freq_end_file, cpu_list)
            with open(perf_summary_file, 'w') as f:
                json.dump(perf_summary, f, indent=2)
            print(f"  [OK] Perf summary saved to {perf_summary_file}")
        except Exception as e:
            print(f"  [ERROR] Failed to parse perf stats: {e}")

        return True

    # -------------------------------------------------------------------------
    # Export / Summary
    # -------------------------------------------------------------------------

    def export_results(self):
        """Export benchmark results to CSV and JSON formats."""
        print("\n>>> Exporting results...")
        pts_results_dir = Path.home() / ".phoronix-test-suite" / "test-results"

        for num_threads in self.thread_list:
            result_name = f"{self.benchmark}-{num_threads}threads"
            # CRITICAL: PTS removes dots from directory names
            # e.g., pgbench-1.17.0-8threads -> pgbench-1170-8threads
            result_dir_name = result_name.replace('.', '')
            result_dir = pts_results_dir / result_dir_name

            if not result_dir.exists():
                print(f"  [WARN] Result not found: {result_dir}")
                print(f"  [INFO] Expected: {result_name}, actual: {result_dir_name}")
                continue

            print(f"  [INFO] Found result directory: {result_dir}")

            # Export to CSV
            csv_output = self.results_dir / f"{num_threads}-thread.csv"
            result = subprocess.run(
                ['phoronix-test-suite', 'result-file-to-csv', result_dir_name],
                capture_output=True, text=True)
            if result.returncode == 0:
                home_csv = Path.home() / f"{result_dir_name}.csv"
                if home_csv.exists():
                    shutil.move(str(home_csv), str(csv_output))
                    print(f"  [OK] Saved CSV: {csv_output}")
            else:
                print(f"  [WARN] CSV export failed for {result_name}: {result.stderr}")

            # Export to JSON
            json_output = self.results_dir / f"{num_threads}-thread.json"
            result = subprocess.run(
                ['phoronix-test-suite', 'result-file-to-json', result_dir_name],
                capture_output=True, text=True)
            if result.returncode == 0:
                home_json = Path.home() / f"{result_dir_name}.json"
                if home_json.exists():
                    shutil.move(str(home_json), str(json_output))
                    print(f"  [OK] Saved JSON: {json_output}")
            else:
                print(f"  [WARN] JSON export failed for {result_name}: {result.stderr}")

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
                    print(f"  [WARN] Failed to read {json_file}: {e}")

        if not all_results:
            print("  [WARN] No results found for summary generation")
            return

        with open(summary_log, 'w') as f:
            f.write("=" * 80 + "\n")
            f.write(f"Benchmark Summary: {self.benchmark}\n")
            f.write(f"Machine: {self.machine_name}\n")
            f.write(f"Test Category: {self.test_category}\n")
            f.write("=" * 80 + "\n\n")
            for result in all_results:
                f.write(f"Threads: {result['threads']}\n")
                f.write(f"  Test: {result['test_name']}\n")
                f.write(f"  Description: {result['description']}\n")
                # None guard: result['value'] may be None on test failure
                val_str = f"{result['value']:.2f}" if result['value'] is not None else "FAILED"
                f.write(f"  Average: {val_str} {result['unit']}\n\n")
        print(f"  [OK] Summary log saved: {summary_log}")

        summary_data = {
            "benchmark": self.benchmark,
            "test_category": self.test_category,
            "machine": self.machine_name,
            "vcpu_count": self.vcpu_count,
            "results": all_results
        }
        with open(summary_json_file, 'w') as f:
            json.dump(summary_data, f, indent=2)
        print(f"  [OK] Summary JSON saved: {summary_json_file}")

    # -------------------------------------------------------------------------
    # Main flow
    # -------------------------------------------------------------------------

    def run(self):
        """Main execution flow."""
        print(f"\n{'#'*80}")
        print(f"# PTS Runner: {self.benchmark_full}")
        print(f"# Machine: {self.machine_name}")
        print(f"# OS: {self.os_name}")
        print(f"# vCPU Count: {self.vcpu_count}")
        print(f"# Thread List: {self.thread_list}")
        if self.quick_mode:
            print("# Quick Mode: ENABLED (FORCE_TIMES_TO_RUN=1)")
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
        # Do NOT remove the entire results_dir — it would destroy parallel thread results.
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

        print(f"[INFO] Install check -> info:{info_installed}, "
              f"test-installed:{test_installed_ok}, dir:{installed_dir_exists}")

        if not already_installed and installed_dir_exists:
            print("[WARN] Existing install directory found but PTS does not report it as installed. "
                  "Treating as broken install and reinstalling.")

        if not already_installed:
            self.install_benchmark()
        else:
            print(f"[INFO] Benchmark already installed, skipping: {self.benchmark_full}")

        # Patch test options to limit configurations (if filters specified)
        self.patch_test_options()

        # Run for each thread count
        failed = []
        try:
            for num_threads in self.thread_list:
                if not self.run_benchmark(num_threads):
                    failed.append(num_threads)
        finally:
            print("\n>>> Final cleanup...")
            self.cleanup_postgresql()
            self.restore_test_options()

        # Export results
        self.export_results()

        # Generate summary
        self.generate_summary()

        # Post-benchmark cleanup (after export/summary)
        cleanup_pts_artifacts(self.benchmark)

        if failed:
            print(f"\n[WARN] Some tests failed: {failed}")
            return False

        print("\n[SUCCESS] All tests completed successfully")
        return True


def main():
    parser = argparse.ArgumentParser(
        description='PostgreSQL pgbench-1.17.0 Runner',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                          # Scaling mode (nproc/4, nproc/2, nproc*3/4, nproc)
  %(prog)s 8                        # Single run with 8 threads
  %(prog)s --threads 16 --quick     # Quick run with 16 threads
  %(prog)s --scaling-factors 1000 --clients 50,100 --modes rw
        """
    )

    parser.add_argument(
        'threads_pos', nargs='?', type=int,
        help='Number of threads (optional, omit for scaling mode)')
    parser.add_argument(
        '--threads', type=int,
        help='Run with specified number of threads only (1 to CPU count)')
    parser.add_argument(
        '--quick', action='store_true',
        help='Quick mode: Run each test only once (FORCE_TIMES_TO_RUN=1)')
    parser.add_argument(
        '--scaling-factors', type=str, default='1000',
        help=('Comma-separated scaling factors to test. '
              f'Default: "1000". Use "all" for all ({",".join(PgbenchRunner.VALID_SCALING_FACTORS)})'))
    parser.add_argument(
        '--clients', type=str, default='50',
        help=('Comma-separated client counts to test. '
              f'Default: "50". Use "all" for all ({",".join(PgbenchRunner.VALID_CLIENTS)})'))
    parser.add_argument(
        '--modes', type=str, default='both',
        choices=['rw', 'ro', 'both', 'all'],
        help='Test mode: rw=Read Write, ro=Read Only, both/all=both. Default: "both"')

    args = parser.parse_args()

    if args.quick:
        print("[INFO] Quick mode enabled: FORCE_TIMES_TO_RUN=1")

    scaling_factors = None
    if args.scaling_factors and args.scaling_factors.lower() != 'all':
        scaling_factors = [s.strip() for s in args.scaling_factors.split(',')]

    clients = None
    if args.clients and args.clients.lower() != 'all':
        clients = [s.strip() for s in args.clients.split(',')]

    modes = args.modes if args.modes != 'all' else None

    # Resolve threads argument (--threads takes priority over positional)
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
