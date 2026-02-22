#!/usr/bin/env python3
"""
PTS Runner for rustls-1.0.0

System Dependencies (from phoronix-test-suite info):
- Software Dependencies:
  * C/C++ Compiler Toolchain
  * Rust
  * Curl
- Estimated Install Time: 69 Seconds
- Environment Size: 896 MB
- Test Type: Processor
- Supported Platforms: Linux, BSD, MacOSX

Test Characteristics:
- Multi-threaded: Yes (built-in multi-threaded Rustls benchmark)
- THFix_in_compile: false - Thread count NOT fixed at compile time
- THChange_at_runtime: true - Runtime thread configuration via NUM_CPU_CORES
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


class RustlsRunner:
    def __init__(self, threads_arg=None, quick_mode=False):
        """
        Initialize Rustls benchmark runner.

        Args:
            threads_arg: Thread count argument (None for scaling mode, int for fixed mode)
            quick_mode: If True, run tests once (FORCE_TIMES_TO_RUN=1) for development
        """
        self.benchmark = "rustls-1.0.0"
        self.benchmark_full = f"pts/{self.benchmark}"
        self.test_category = "Cryptography and TLS"
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

            # Remove any zeros and deduplicate

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

        # Rust/cargo environment locations
        self.home_dir = Path.home()
        self.cargo_home = self.home_dir / ".cargo"
        self.rustup_home = self.home_dir / ".rustup"
        self.cargo_env_file = self.cargo_home / "env"
        self.required_toolchain = os.environ.get("PTS_RUST_TOOLCHAIN", os.environ.get("RUST_TOOLCHAIN", "1.84.0"))
        self.make_jobs = os.cpu_count() or 1

        # Source cargo env in subshells when available
        if self.cargo_env_file.exists():
            self.shell_env_prefix = f'. "{self.cargo_env_file}" && '
        else:
            self.shell_env_prefix = ""
            print("  [WARN] ~/.cargo/env not found; ensure cargo is available in PATH")

        self.base_env = self.build_base_env()

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
        """
        Get OS name and version formatted as <Distro>_<Version>.
        Example: Ubuntu_22_04
        """
        try:
            # Try lsb_release first
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
                distro = info['NAME'].split()[0]
                version = info['VERSION_ID'].replace('.', '_')
                return f"{distro}_{version}"
        except Exception:
            pass

        return "Unknown_OS"

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

    def is_wsl(self):
        """
        Detect if running in WSL environment (for logging purposes only).
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
            str: Comma-separated perf event list, or None if unavailable
        """
        import shutil

        # 1. Check if perf command exists in PATH
        perf_path = shutil.which("perf")
        if not perf_path:
            print("  [INFO] perf command not found in PATH")
            return None

        # 2. Test Hardware + Software events (Preferred for Native Linux)
        hw_events = "cycles,instructions,cpu-clock,task-clock,context-switches,cpu-migrations"
        test_cmd = f"{perf_path} stat -e {hw_events} sleep 0.01 2>&1"

        try:
            result = subprocess.run(
                ['bash', '-c', test_cmd],
                capture_output=True,
                text=True,
                timeout=3
            )

            output = result.stdout + result.stderr

            # Check if all events are supported
            if result.returncode == 0 and '<not supported>' not in output:
                print(f"  [OK] Hardware PMU available: {hw_events}")
                return hw_events

            # 3. Test Software-only events (Fallback for Cloud/VM/Standard WSL)
            sw_events = "cpu-clock,task-clock,context-switches,cpu-migrations"
            test_sw_cmd = f"{perf_path} stat -e {sw_events} sleep 0.01 2>&1"
            result_sw = subprocess.run(
                ['bash', '-c', test_sw_cmd],
                capture_output=True,
                text=True,
                timeout=3
            )

            if result_sw.returncode == 0:
                print(f"  [INFO] Hardware PMU not available. Using software events: {sw_events}")
                return sw_events

        except subprocess.TimeoutExpired:
            print("  [WARN] perf test timed out")
        except Exception as e:
            print(f"  [DEBUG] perf test execution failed: {e}")

        print("  [INFO] perf command exists but is not functional (permission or kernel issue)")
        return None

    def build_base_env(self):
        """Construct a deterministic environment for PTS commands."""
        env = os.environ.copy()

        cargo_bin = str(self.cargo_home / "bin")
        current_path = env.get("PATH", "")
        path_entries = current_path.split(":") if current_path else []
        if cargo_bin not in path_entries:
            env["PATH"] = f"{cargo_bin}:{current_path}" if current_path else cargo_bin

        env.setdefault("CARGO_HOME", str(self.cargo_home))
        env.setdefault("RUSTUP_HOME", str(self.rustup_home))
        env.setdefault("RUSTUP_TOOLCHAIN", self.required_toolchain)
        env.setdefault("CARGO_HTTP_CHECK_REVOKE", "false")
        env.setdefault("CARGO_NET_GIT_FETCH_WITH_CLI", "true")
        env.setdefault("CARGO_BUILD_JOBS", str(self.make_jobs))

        ca_bundle = env.get("CURL_CA_BUNDLE")
        custom_bundle = self.cargo_home / "rust-combined-ca.pem"
        if not ca_bundle:
            if custom_bundle.exists():
                ca_bundle = str(custom_bundle)
            elif Path("/etc/ssl/certs/ca-certificates.crt").exists():
                ca_bundle = "/etc/ssl/certs/ca-certificates.crt"

        if ca_bundle:
            env.setdefault("CURL_CA_BUNDLE", ca_bundle)
            env.setdefault("SSL_CERT_FILE", ca_bundle)

        return env

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

    def patch_install_script(self):
        """
        Patch install.sh to fix PTS upstream bugs:
        1. Fix incomplete chmod line (chmod +x rustls > \\)
        2. Fix hardcoded --threads 24 to use NUM_CPU_CORES environment variable

        Background:
        - Issue 1: Original install.sh ends with 'chmod +x rustls > \' (syntax error)
        - Issue 2: Hardcoded '--threads 24' ignores NUM_CPU_CORES environment variable
        - Root cause: PTS test-profile rustls-1.0.0 is new (2023-2024) and lacks maturity
        - Impact: Thread configuration is broken, causing failures with certain thread counts
        """
        install_sh_path = Path.home() / '.phoronix-test-suite' / 'test-profiles' / 'pts' / self.benchmark / 'install.sh'

        if not install_sh_path.exists():
            print(f"  [WARN] install.sh not found at {install_sh_path}")
            return False

        try:
            with open(install_sh_path, 'r') as f:
                content = f.read()

            original_content = content
            patched = False

            # Patch 1: Fix incomplete chmod line
            if content.strip().endswith('chmod +x rustls > \\'):
                print("  [INFO] Patching install.sh: fixing incomplete chmod line...")
                content = content.rstrip('> \\\n')
                if not content.endswith('chmod +x rustls'):
                    content = content + '\nchmod +x rustls'
                patched = True

            # Patch 2: Fix hardcoded --threads 24
            if '--threads 24' in content:
                print("  [INFO] Patching install.sh: replacing hardcoded --threads 24 with NUM_CPU_CORES...")
                content = content.replace('--threads 24', '--threads ${NUM_CPU_CORES:-24}')
                patched = True

            # Write back if changes were made
            if patched:
                with open(install_sh_path, 'w') as f:
                    f.write(content)
                    if not content.endswith('\n'):
                        f.write('\n')

                print("  [OK] install.sh patched successfully")
                chmod_check = original_content.strip().endswith('chmod +x rustls > \\\\')
                threads_check = '--threads 24' in original_content
                print(f"       - chmod line fixed: {chmod_check}")
                print(f"       - threads parameter fixed: {threads_check}")
                return True
            else:
                print("  [OK] install.sh already patched or correct")
                return True

        except Exception as e:
            print(f"  [ERROR] Failed to patch install.sh: {e}")
            return False

    def install_benchmark(self):
        """
        Install rustls-1.0.0 with native compilation.

        Since THFix_in_compile=false, NUM_CPU_CORES is NOT set during build.
        Thread count is controlled at runtime via NUM_CPU_CORES environment variable.
        """
        print(f"\n>>> Installing {self.benchmark_full}...")

        # Remove existing installation first
        print("  [INFO] Removing existing installation...")
        remove_cmd = f'echo "y" | phoronix-test-suite remove-installed-test "{self.benchmark_full}"'
        print(f"  [INSTALL CMD] {remove_cmd}")
        subprocess.run(
            ['bash', '-c', remove_cmd],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=self.base_env
        )

        # Patch install.sh for PTS upstream bug
        self.patch_install_script()

        # Build install command with environment variables
        # Note: NUM_CPU_CORES is NOT set here because THFix_in_compile=false
        # Thread control is done at runtime, not compile time
        # Use batch-install to suppress prompts
        # MAKEFLAGS: parallelize compilation itself with -j$(nproc)
        # CARGO_HTTP_CHECK_REVOKE: disable SSL cert revocation check
        # GIT_SSL_NO_VERIFY: disable git SSL verification for cargo dependencies
        install_cmd = (
            f'{self.shell_env_prefix}'
            f'RUSTUP_TOOLCHAIN={self.required_toolchain} '
            f'CARGO_HOME="{self.cargo_home}" '
            f'RUSTUP_HOME="{self.rustup_home}" '
            f'CARGO_HTTP_CHECK_REVOKE=false '
            f'GIT_SSL_NO_VERIFY=true '
            f'MAKEFLAGS="-j{self.make_jobs}" '
            f'phoronix-test-suite batch-install {self.benchmark_full}'
        )

        # Print install command for debugging (as per README requirement)
        print(f"\n{'>'*80}")
        print("[PTS INSTALL COMMAND]")
        print(f"  {install_cmd}")
        print(f"{'<'*80}\n")

        # Execute install command with real-time output
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
            bufsize=1,
            env=self.base_env
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
            text=True,
            env=self.base_env
        )

        if verify_result.returncode != 0:
            print("  [WARN] Test may not be fully installed (test-installed check failed)")
            print("  [INFO] But installation directory exists, continuing...")

        print(f"  [OK] Installation completed and verified: {installed_dir}")

    def parse_perf_stats_and_freq(self, perf_stats_file, freq_start_file, freq_end_file, cpu_list):
        """
        Parse perf stat output and CPU frequency files.
        Handles both hardware and software-only perf events gracefully.
        """
        # If perf monitoring was disabled, return minimal info
        if not self.perf_events or not perf_stats_file.exists():
            return {
                'note': 'perf monitoring not available',
                'cpu_list': cpu_list
            }

        cpu_ids = [int(c.strip()) for c in cpu_list.split(',')]
        per_cpu_metrics = {cpu_id: {} for cpu_id in cpu_ids}

        # Parse perf stat output with flexible regex
        with open(perf_stats_file, 'r') as f:
            for line in f:
                # Match: "CPU0  123,456  cycles" or "CPU0  <not supported>  cycles"
                match = re.match(r'CPU(\d+)\s+([\d,.<>a-zA-Z\s]+)\s+([a-zA-Z0-9\-_]+)', line)
                if match:
                    cpu_num = int(match.group(1))
                    value_str = match.group(2).strip()
                    event = match.group(3)

                    if cpu_num in per_cpu_metrics and '<not supported>' not in value_str:
                        try:
                            # Remove units like "msec" if present (e.g. "123.45 msec" -> "123.45")
                            value_clean = value_str.split()[0]
                            value = float(value_clean.replace(',', ''))
                            per_cpu_metrics[cpu_num][event] = value
                        except ValueError:
                            continue

        # Calculate metrics (IPC, frequency, utilization)
        # For simplicity, return the parsed data
        return {'per_cpu_metrics': per_cpu_metrics, 'cpu_list': cpu_list}

    def run_benchmark(self, num_threads):
        """Run benchmark with conditional perf monitoring."""
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

        # Build PTS base command (taskset if needed)
        if num_threads >= self.vcpu_count:
            cpu_list = ','.join([str(i) for i in range(self.vcpu_count)])
            pts_base_cmd = f'phoronix-test-suite batch-run {self.benchmark_full}'
        else:
            cpu_list = self.get_cpu_affinity_list(num_threads)
            pts_base_cmd = f'taskset -c {cpu_list} phoronix-test-suite batch-run {self.benchmark_full}'

        # Environment variables for batch mode execution
        # MUST USE {self.benchmark} - DO NOT HARDCODE BENCHMARK NAME
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

        # Construct Final Command with conditional perf
        if self.perf_events:
            # Perf available - check if we can use per-CPU breakdown
            if self.perf_paranoid <= 0:
                # Full monitoring mode with per-CPU metrics
                perf_cmd = f"perf stat -e {self.perf_events} -A -a -o {perf_stats_file}"
                print("  [INFO] Running with perf monitoring (per-CPU mode)")
            else:
                # Limited mode without per-CPU breakdown
                perf_cmd = f"perf stat -e {self.perf_events} -o {perf_stats_file}"
                print("  [INFO] Running with perf monitoring (aggregated mode)")

            pts_cmd = f'NUM_CPU_CORES={num_threads} {batch_env} {perf_cmd} {pts_base_cmd}'
        else:
            # Perf unavailable
            pts_cmd = f'NUM_CPU_CORES={num_threads} {batch_env} {pts_base_cmd}'
            print("  [INFO] Running without perf")

        # Record CPU frequency before benchmark
        # Uses cross-platform method (works on x86_64, ARM64, and cloud VMs)
        print("[INFO] Recording CPU frequency before benchmark...")
        if self.record_cpu_frequency(freq_start_file):
            print("  [OK] Start frequency recorded")
        else:
            print("  [WARN] CPU frequency not available (common on ARM64/cloud VMs)")

        # Execute benchmark with real-time output streaming
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
                bufsize=1,
                env=self.base_env
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
            # Parse perf stats if available
            if self.perf_events and perf_stats_file.exists():
                try:
                    perf_summary = self.parse_perf_stats_and_freq(
                        perf_stats_file, freq_start_file, freq_end_file, cpu_list
                    )
                    with open(perf_summary_file, 'w') as f:
                        json.dump(perf_summary, f, indent=2)
                except Exception as e:
                    print(f"  [ERROR] Failed to parse perf stats: {e}")
            return True
        else:
            print(f"\n[ERROR] Benchmark failed with return code {returncode}")
            return False

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
                print(f"[WARN] Result not found: {result_dir}")
                print(f"[INFO] Expected: {result_name}, actual: {result_dir_name}")
                continue

            print(f"[DEBUG] result_name: {result_name}, result_dir_name: {result_dir_name}")

            # Export to CSV - Use result_dir_name (dots removed)
            csv_output = self.results_dir / f"{num_threads}-thread.csv"
            print(f"  [EXPORT] CSV: {csv_output}")
            result = subprocess.run(
                ['phoronix-test-suite', 'result-file-to-csv', result_dir_name],
                capture_output=True,
                text=True,
                env=self.base_env
            )
            if result.returncode == 0:
                home_csv = Path.home() / f"{result_dir_name}.csv"
                if home_csv.exists():
                    shutil.move(str(home_csv), str(csv_output))
                    print(f"  [OK] Saved: {csv_output}")
            else:
                print(f"  [WARN] CSV export failed: {result.stderr}")

            # Export to JSON - Use result_dir_name (dots removed)
            json_output = self.results_dir / f"{num_threads}-thread.json"
            print(f"  [EXPORT] JSON: {json_output}")
            result = subprocess.run(
                ['phoronix-test-suite', 'result-file-to-json', result_dir_name],
                capture_output=True,
                text=True,
                env=self.base_env
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
            f.write("Rustls Benchmark Summary\n")
            f.write(f"Machine: {self.machine_name}\n")
            f.write(f"Test Category: {self.test_category}\n")
            f.write("="*80 + "\n\n")

            for result in all_results:
                f.write(f"Threads: {result['threads']}\n")
                f.write(f"  Test: {result['test_name']}\n")
                f.write(f"  Description: {result['description']}\n")
                val_str = f"{result['value']:.2f}" if result['value'] is not None else "FAILED"
                f.write(f"  Average: {val_str} {result['unit']}\n\n")

        print(f"[OK] Summary log saved: {summary_log}")

        # Generate summary.json (AI-friendly format)
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
        print("Rustls Benchmark Runner")
        print(f"{'='*80}")
        print(f"[INFO] Machine: {self.machine_name}")
        print(f"[INFO] vCPU count: {self.vcpu_count}")
        print(f"[INFO] Test category: {self.test_category}")
        print("[INFO] Thread mode: Runtime configurable (THChange_at_runtime=true)")
        print(f"[INFO] Threads to test: {self.thread_list}")
        print(f"[INFO] Results directory: {self.results_dir}")
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

        # Install benchmark once (not per thread count, since THFix_in_compile=false)
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

        # Run for each thread count
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
        description="Rustls Benchmark Runner",
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

    runner = RustlsRunner(threads_arg=threads, quick_mode=args.quick)
    success = runner.run()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
