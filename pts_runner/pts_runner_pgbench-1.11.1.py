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
from pathlib import Path


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
    def __init__(self, threads_arg=None, quick_mode=False):
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
            self.thread_list = list(range(1, self.vcpu_count + 1))
        else:
            n = min(threads_arg, self.vcpu_count)
            self.thread_list = [n]

        # Project structure
        self.script_dir = Path(__file__).parent.resolve()
        self.project_root = self.script_dir.parent
        self.results_dir = self.project_root / "results" / self.machine_name / self.os_name / self.test_category_dir / self.benchmark

        # Quick mode for development
        self.quick_mode = quick_mode

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

    def install_benchmark(self):
        """Install benchmark using standard PTS mechanism."""
        print(f"\n>>> Checking for large files to pre-seed...")
        downloader = PreSeedDownloader()
        downloader.download_from_xml(self.benchmark_full, threshold_mb=96)

        print(f"\n>>> Installing {self.benchmark_full}...")

        # PATCH: Fix install.sh before installing
        self.patch_install_script()

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
            install_output.append(line)

        process.wait()
        returncode = process.returncode

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
            
            # Replace $LOG_FILE redirects with stdout output
            # Pattern 1: echo "Buffer size..." > $LOG_FILE
            # Pattern 2: pgbench ... >>$LOG_FILE 2>&1
            patched_content = script_content.replace(
                'echo "Buffer size is ${SHARED_BUFFER_SIZE}MB" > $LOG_FILE',
                'echo "Buffer size is ${SHARED_BUFFER_SIZE}MB"'
            ).replace(
                '>>$LOG_FILE 2>&1',
                '2>&1'
            )
            
            with open(pgbench_script, 'w') as f:
                f.write(patched_content)
            
            print(f"  [OK] pgbench script patched to output to stdout")

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

    def run_benchmark(self, num_threads):
        """Run benchmark with specified thread count."""
        print(f"\n{'='*80}")
        print(f">>> Running {self.benchmark_full} with {num_threads} thread(s)")
        print(f"{'='*80}")

        
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
        print(f"{'#'*80}")

        # Clean results directory
        if self.results_dir.exists():
            print(f"\n>>> Cleaning existing results directory: {self.results_dir}")
            shutil.rmtree(self.results_dir)

        self.results_dir.mkdir(parents=True, exist_ok=True)

        # Install
        self.install_benchmark()

        # Run for each thread count
        failed = []
        for num_threads in self.thread_list:
            if not self.run_benchmark(num_threads):
                failed.append(num_threads)

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

    args = parser.parse_args()

    if args.quick:
        print("[INFO] Quick mode enabled: FORCE_TIMES_TO_RUN=1")
        print("[INFO] Tests will run once instead of 3+ times (60-70%% time reduction)")

    # Resolve threads argument (prioritize --threads if both provided)
    threads = args.threads if args.threads is not None else args.threads_pos

    runner = PgbenchRunner(threads_arg=threads, quick_mode=args.quick)
    success = runner.run()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
