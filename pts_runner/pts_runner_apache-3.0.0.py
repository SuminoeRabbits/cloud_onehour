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
- Multi-threaded: No (single-threaded benchmark)
- THFix_in_compile: false
- THChange_at_runtime: false
- TH_scaling: single-threaded

Platform Notes:
- EL10 (RHEL 10 / Oracle Linux 10):
  * pcre-devel dropped from repos; run scripts_rhel9/setup_pcre.sh first.
    setup_pcre.sh builds PCRE 8.45 static to /usr/local.
    This runner prepends PATH=/usr/local/bin so ./configure finds pcre-config.
  * apr-util vs libxml2-2.12.x ABI change: -Wno-error=incompatible-pointer-types
    and -Wno-error=int-conversion added to CFLAGS.
- GCC-14 compatibility (all platforms):
  * install.sh is patched before batch-install to fix wrk/OpenSSL 1.1.1i build.
  * setup_gcc14.sh creates /usr/bin/gcc-14 symlinks on RHEL/OL so CC=gcc-14 works.
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
        """Parse downloads.xml for the benchmark and download large files."""
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
                        self.ensure_file(urls, filename, size_bytes=size_bytes)
        except Exception as e:
            print(f"  [ERROR] Failed to parse downloads.xml: {e}")
            return False
        return True

    def get_remote_file_size(self, url):
        try:
            cmd = ['curl', '-s', '-I', '-L', url]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                return -1
            for line in result.stdout.splitlines():
                if line.lower().startswith('content-length:'):
                    try:
                        return int(line.split(':')[1].strip())
                    except ValueError:
                        pass
        except Exception:
            pass
        return -1

    def ensure_file(self, urls, filename, size_bytes=-1):
        _LARGE_FILE_THRESHOLD_BYTES = 10 * 1024 * 1024 * 1024
        target_path = self.cache_dir / filename
        if target_path.exists():
            if size_bytes > 0:
                actual = target_path.stat().st_size
                if actual == size_bytes:
                    print(f"  [CACHE] Verified: {filename}")
                    return True
                else:
                    print(f"  [WARN] Incomplete cache: {filename} ({actual}/{size_bytes} bytes). Resuming...")
            else:
                print(f"  [CACHE] File found: {filename}")
                return True
        if isinstance(urls, str):
            urls = [urls]
        num_conn = 4 if size_bytes > 0 and size_bytes >= _LARGE_FILE_THRESHOLD_BYTES else 16
        print(f"  [ARIA2] Downloading {filename} with {num_conn} connections...")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            "aria2c", "-x", str(num_conn), "-s", str(num_conn),
            "--continue=true", "--connect-timeout=30", "--timeout=120",
            "--max-tries=2", "--retry-wait=5",
            "-d", str(self.cache_dir), "-o", filename,
        ] + urls
        try:
            subprocess.run(cmd, check=True, timeout=5400)
            print(f"  [aria2c] Download completed: {filename}")
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            print(f"  [WARN] aria2c download failed: {e}")
            if target_path.exists():
                target_path.unlink()
            return False


class ApacheRunner:
    def __init__(self, threads_arg=None, quick_mode=False):
        """
        Initialize Apache HTTP Server benchmark runner.

        CRITICAL: This is a single-threaded benchmark (TH_scaling=single-threaded,
        THChange_at_runtime=false).  thread_list is always [1] regardless of
        threads_arg or system vCPU count.
        """
        self.benchmark = "apache-3.0.0"
        self.benchmark_full = f"pts/{self.benchmark}"
        self.test_category = "Network"
        self.test_category_dir = self.test_category.replace(" ", "_")

        self.vcpu_count = os.cpu_count() or 1
        self.machine_name = os.environ.get('MACHINE_NAME', os.uname().nodename)
        self.os_name = self.get_os_name()

        # CRITICAL: Single-threaded benchmark — thread_list is always [1]
        self.thread_list = [1]

        self.script_dir = Path(__file__).parent.resolve()
        self.project_root = self.script_dir.parent
        self.results_dir = self.project_root / "results" / self.machine_name / self.os_name / self.test_category_dir / self.benchmark

        self.quick_mode = quick_mode

        self.is_wsl_env = self.is_wsl()
        if self.is_wsl_env:
            print("  [INFO] Running on WSL environment")

        self.ensure_upload_disabled()

        # CRITICAL: setup perf permissions BEFORE testing perf availability
        self.perf_paranoid = self.check_and_setup_perf_permissions()
        self.perf_events = self.get_perf_events()
        if self.perf_events:
            print(f"  [OK] Perf monitoring enabled with events: {self.perf_events}")
        else:
            print("  [INFO] Perf monitoring disabled (command missing or unsupported)")

    # ------------------------------------------------------------------
    # Basic helpers
    # ------------------------------------------------------------------

    def get_os_name(self):
        """Return OS name as <Distro>_<Version>, e.g. Ubuntu_22_04."""
        try:
            result = subprocess.run(
                "lsb_release -d -s".split(), capture_output=True, text=True
            )
            if result.returncode == 0:
                parts = result.stdout.strip().split()
                if len(parts) >= 2:
                    return f"{parts[0]}_{parts[1].replace('.', '_')}"
        except Exception:
            pass
        try:
            with open('/etc/os-release') as f:
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
        try:
            if not os.path.exists('/proc/version'):
                return False
            with open('/proc/version') as f:
                content = f.read().lower()
            return 'microsoft' in content or 'wsl' in content
        except Exception:
            return False

    def ensure_upload_disabled(self):
        config_path = Path.home() / ".phoronix-test-suite" / "user-config.xml"
        if not config_path.exists():
            return
        try:
            content = config_path.read_text()
            if '<UploadResults>TRUE</UploadResults>' in content:
                print("  [WARN] UploadResults is TRUE in user-config.xml. Disabling...")
                config_path.write_text(
                    content.replace('<UploadResults>TRUE</UploadResults>',
                                    '<UploadResults>FALSE</UploadResults>')
                )
                print("  [OK] UploadResults set to FALSE")
        except Exception as e:
            print(f"  [WARN] Failed to check/update user-config.xml: {e}")

    def get_cpu_affinity_list(self, n):
        """Generate CPU affinity list preferring physical cores first."""
        half = self.vcpu_count // 2
        if n <= half:
            cpu_list = [str(i * 2) for i in range(n)]
        else:
            cpu_list = [str(i * 2) for i in range(half)]
            cpu_list.extend([str(i * 2 + 1) for i in range(n - half)])
        return ','.join(cpu_list)

    # ------------------------------------------------------------------
    # CPU frequency monitoring
    # ------------------------------------------------------------------

    def get_cpu_frequencies(self):
        """Get CPU frequencies in kHz (cross-platform: x86_64, ARM64, cloud VMs)."""
        frequencies = []
        try:
            result = subprocess.run(
                ['bash', '-c', 'grep "cpu MHz" /proc/cpuinfo'],
                capture_output=True, text=True
            )
            if result.returncode == 0 and result.stdout.strip():
                for line in result.stdout.strip().split('\n'):
                    parts = line.split(':')
                    if len(parts) >= 2:
                        frequencies.append(int(float(parts[1].strip()) * 1000))
                if frequencies:
                    return frequencies
        except Exception:
            pass
        try:
            freq_files = sorted(
                Path('/sys/devices/system/cpu').glob('cpu[0-9]*/cpufreq/scaling_cur_freq')
            )
            if not freq_files:
                freq_files = sorted(
                    Path('/sys/devices/system/cpu').glob('cpu[0-9]*/cpufreq/cpuinfo_cur_freq')
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
        frequencies = self.get_cpu_frequencies()
        try:
            with open(output_file, 'w') as f:
                for freq in frequencies:
                    f.write(f"{freq}\n")
            return bool(frequencies)
        except Exception as e:
            print(f"  [WARN] Failed to write frequency file: {e}")
            return False

    # ------------------------------------------------------------------
    # Perf monitoring
    # ------------------------------------------------------------------

    def get_perf_events(self):
        """Detect available perf events (3-level fallback)."""
        perf_path = shutil.which("perf")
        if not perf_path:
            print("  [INFO] perf command not found in PATH")
            return None

        hw_events = "cycles,instructions,cpu-clock,task-clock,context-switches,cpu-migrations"
        try:
            result = subprocess.run(
                ['bash', '-c', f"{perf_path} stat -e {hw_events} sleep 0.01 2>&1"],
                capture_output=True, text=True, timeout=3
            )
            output = result.stdout + result.stderr
            if result.returncode == 0 and '<not supported>' not in output:
                print(f"  [OK] Hardware PMU available: {hw_events}")
                return hw_events

            sw_events = "cpu-clock,task-clock,context-switches,cpu-migrations"
            result_sw = subprocess.run(
                ['bash', '-c', f"{perf_path} stat -e {sw_events} sleep 0.01 2>&1"],
                capture_output=True, text=True, timeout=3
            )
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
        """Check/adjust perf_event_paranoid. Returns current value."""
        print(f"\n{'='*80}")
        print(">>> Checking perf_event_paranoid setting")
        print(f"{'='*80}")
        try:
            result = subprocess.run(
                ['cat', '/proc/sys/kernel/perf_event_paranoid'],
                capture_output=True, text=True, check=True
            )
            current_value = int(result.stdout.strip())
            print(f"  [INFO] Current perf_event_paranoid: {current_value}")
            if current_value >= 1:
                print(f"  [WARN] perf_event_paranoid={current_value}: attempting to set to 0...")
                result = subprocess.run(
                    ['sudo', 'sysctl', '-w', 'kernel.perf_event_paranoid=0'],
                    capture_output=True, text=True
                )
                if result.returncode == 0:
                    print("  [OK] perf_event_paranoid adjusted to 0")
                    return 0
                else:
                    print("  [WARN] Could not adjust perf_event_paranoid (running in limited mode)")
                    return current_value
            else:
                print(f"  [OK] perf_event_paranoid={current_value} is acceptable")
                return current_value
        except Exception as e:
            print(f"  [ERROR] Could not check perf_event_paranoid: {e}")
            return 2

    # ------------------------------------------------------------------
    # install.sh patching
    # ------------------------------------------------------------------

    def patch_install_sh(self):
        """
        Patch PTS install.sh BEFORE batch-install for RHEL + GCC-14 compatibility.

        Patches applied:
          P0: rm -rf $HOME/httpd_ before mkdir (fixes APR libapr-1.so symlink error)
          P1: GCC-14 wrk/OpenSSL patches (c99->gnu99, OPENSSL_CFLAGS, CC in make)
          P2: apache wrapper — cap wrk threads to min(NUM_CPU_CORES, connections)
              and add --timeout 30s
          P3: httpd make || exit 1 (surface silent configure/make failures)
        """
        print("\n>>> Patching install.sh for GCC-14 + RHEL compatibility...")

        pts_home = Path.home() / '.phoronix-test-suite'
        install_sh = pts_home / 'test-profiles' / 'pts' / self.benchmark / 'install.sh'

        if not install_sh.exists():
            print(f"  [WARN] install.sh not found: {install_sh}")
            return

        content = install_sh.read_text()
        backup = install_sh.parent / 'install.sh.original'
        if not backup.exists():
            shutil.copy(install_sh, backup)
            print(f"  [INFO] Backed up original: {backup}")

        already_fully_patched = (
            'P0: httpd_ cleanup' in content
            and 'GCC-14 compatibility patch' in content
            and '--timeout 30s' in content
            and 'make -j $NUM_CPU_CORES || exit 1' in content
        )
        if already_fully_patched:
            print("  [INFO] install.sh already fully patched, skipping")
            return

        any_patch_applied = False

        # ------------------------------------------------------------------
        # P0: Clean httpd_ directory before mkdir to fix APR symlink errors
        # ------------------------------------------------------------------
        if 'P0: httpd_ cleanup' not in content:
            old_mkdir = 'mkdir $HOME/httpd_'
            new_mkdir = (
                '# P0: httpd_ cleanup — prevents "libapr-1.so: File exists" on retry\n'
                'rm -rf $HOME/httpd_\n'
                'mkdir $HOME/httpd_'
            )
            if old_mkdir in content:
                content = content.replace(old_mkdir, new_mkdir)
                print("  [OK] P0: Added httpd_ cleanup before mkdir")
                any_patch_applied = True
            else:
                print("  [WARN] P0: Could not find 'mkdir $HOME/httpd_' to patch")

        # ------------------------------------------------------------------
        # P1: GCC-14 wrk/OpenSSL patches
        # ------------------------------------------------------------------
        if 'GCC-14 compatibility patch' not in content:
            gcc14_patch = r'''
# === GCC-14 compatibility patch for wrk's bundled OpenSSL 1.1.1i ===

# P1a: Fix wrk CFLAGS (c99 -> gnu99 for better compatibility)
sed -i 's/CFLAGS  += -std=c99/CFLAGS  += -std=gnu99/' Makefile

# P1b: Add OPENSSL_CFLAGS variable with GCC-14 compatible flags
sed -i '/^OPENSSL_OPTS = /a\OPENSSL_CFLAGS = -std=gnu89 -Wno-error -O2 -march=native' Makefile

# P1c: Modify OpenSSL config to use CC and OPENSSL_CFLAGS
sed -i 's|./config $(OPENSSL_OPTS)|CC=$${CC:-gcc-14} CFLAGS=\\"$(OPENSSL_CFLAGS)\\" ./config $(OPENSSL_OPTS)|' Makefile

# P1d: Pass CC and CFLAGS to OpenSSL make depend
sed -i 's|@$(MAKE) -C $< depend|@$(MAKE) -C $< CC=$${CC:-gcc-14} "CFLAGS=$(OPENSSL_CFLAGS)" depend|' Makefile

# P1e: Pass CC and CFLAGS to OpenSSL make (but not install_sw)
sed -i '/depend/,/install_sw/{s|@$(MAKE) -C $<$|@$(MAKE) -C $< CC=$${CC:-gcc-14} "CFLAGS=$(OPENSSL_CFLAGS)"|;}' Makefile

echo "[PATCH] GCC-14 compatibility patch applied to wrk Makefile"
# === End of GCC-14 compatibility patch ===

'''
            old_wrk_build = 'cd wrk-4.2.0\nmake -j $NUM_CPU_CORES'
            new_wrk_build = 'cd wrk-4.2.0' + gcc14_patch + 'make -j $(nproc)'

            if old_wrk_build in content:
                content = content.replace(old_wrk_build, new_wrk_build)
                print("  [OK] P1: Applied GCC-14 wrk/OpenSSL patches")
                any_patch_applied = True
            else:
                print("  [WARN] P1: Could not find wrk build section to patch")
        else:
            print("  [INFO] P1: GCC-14 patches already applied")

        # ------------------------------------------------------------------
        # P2: apache wrapper — cap threads to min(NUM_CPU_CORES, connections)
        #     and add --timeout 30s
        # ------------------------------------------------------------------
        if '--timeout 30s' not in content:
            old_wrapper = r'./wrk-4.2.0/wrk -t \$NUM_CPU_CORES \$@ > \$LOG_FILE 2>&1'
            new_wrapper = (
                r'# P2: cap wrk threads to min(NUM_CPU_CORES, connections)' + '\n'
                r'CONNECTIONS=1' + '\n'
                r'for arg in "\$@"; do' + '\n'
                r'    case "\$prev_arg" in' + '\n'
                r'        -c) CONNECTIONS="\$arg" ;;' + '\n'
                r'    esac' + '\n'
                r'    prev_arg="\$arg"' + '\n'
                r'done' + '\n'
                r'THREADS=\$NUM_CPU_CORES' + '\n'
                r'if [ "\$CONNECTIONS" -lt "\$THREADS" ]; then' + '\n'
                r'    THREADS=\$CONNECTIONS' + '\n'
                r'fi' + '\n'
                r'./wrk-4.2.0/wrk -t \$THREADS --timeout 30s "\$@" > \$LOG_FILE 2>&1'
            )
            if old_wrapper in content:
                content = content.replace(old_wrapper, new_wrapper)
                print("  [OK] P2: Patched apache wrapper (thread cap + --timeout 30s)")
                any_patch_applied = True
            else:
                print("  [WARN] P2: Could not find apache wrapper pattern to patch")
        else:
            print("  [INFO] P2: apache wrapper already patched")

        # ------------------------------------------------------------------
        # P3: httpd make || exit 1 — surface silent configure/make failures
        # ------------------------------------------------------------------
        if 'make -j $NUM_CPU_CORES || exit 1' not in content:
            old_make = '\tmake -j $NUM_CPU_CORES\n\tmake install'
            new_make = '\tmake -j $NUM_CPU_CORES || exit 1\n\tmake install'
            if old_make in content:
                content = content.replace(old_make, new_make)
                print("  [OK] P3: Added '|| exit 1' to httpd make")
                any_patch_applied = True
            else:
                print("  [INFO] P3: httpd make error detection already applied or pattern not found")
        else:
            print("  [INFO] P3: httpd make error detection already applied")

        if any_patch_applied:
            install_sh.write_text(content)
            print("  [OK] install.sh written with patches")
        else:
            print("  [INFO] install.sh already fully patched, nothing changed")

    # ------------------------------------------------------------------
    # Installation
    # ------------------------------------------------------------------

    def _show_install_log_tail(self, install_log, tail=60):
        """Print the last N lines of install_log for post-mortem debugging."""
        try:
            log_path = Path(install_log)
            if not log_path.exists():
                print(f"  [INFO] Install log not found: {log_path}")
                return
            lines = log_path.read_text(errors='ignore').splitlines()
            if not lines:
                print(f"  [INFO] Install log is empty: {log_path}")
                return
            shown = lines[-tail:]
            print(f"\n  [INFO] Install log tail (last {len(shown)} lines) — {log_path}:")
            print("  " + "-" * 76)
            for line in shown:
                print(f"  {line}")
            print("  " + "-" * 76)
        except Exception as e:
            print(f"  [WARN] Could not read install log: {e}")

    def check_install_prerequisites(self):
        """
        Pre-flight check: verify all required build tools before attempting install.

        Fails fast with a clear message rather than letting the build fail silently
        inside PTS (PTS often swallows install.sh stderr making failures invisible).
        """
        print("\n>>> Checking build prerequisites for apache-3.0.0...")
        issues = []

        for tool, hint in [
            ('gcc-14',  'Run scripts/prepare_tools.sh (Ubuntu) or scripts_rhel9/prepare_tools.sh (EL)'),
            ('g++-14',  'Run scripts/prepare_tools.sh (Ubuntu) or scripts_rhel9/prepare_tools.sh (EL)'),
            ('make',    'Install build-essential (Ubuntu) or "Development Tools" group (EL)'),
            ('perl',    'Install perl (Ubuntu: apt install perl  /  EL: dnf install perl)'),
        ]:
            if shutil.which(tool):
                result = subprocess.run([tool, '--version'], capture_output=True, text=True)
                ver = result.stdout.splitlines()[0] if result.returncode == 0 else 'unknown'
                print(f"  [OK] {tool}: {ver}")
            else:
                issues.append(f"{tool} not found in PATH — {hint}")

        # pcre-config: check /usr/local/bin first (EL10 builds to /usr/local via setup_pcre.sh)
        pcre_config = (
            shutil.which('pcre-config')
            or ('/usr/local/bin/pcre-config' if Path('/usr/local/bin/pcre-config').exists() else None)
        )
        if pcre_config:
            print(f"  [OK] pcre-config: {pcre_config}")
        else:
            issues.append(
                "pcre-config not found — install libpcre3-dev (Ubuntu) or run "
                "scripts_rhel9/setup_pcre.sh (EL10, where pcre-devel is dropped)"
            )

        if issues:
            print("\n  [ERROR] Missing build prerequisites:")
            for issue in issues:
                print(f"    - {issue}")
            print("\n  [INFO] Re-run the appropriate prepare_tools.sh before running this benchmark.")
            sys.exit(1)

        print("  [OK] All prerequisites satisfied\n")

    def install_benchmark(self):
        """
        Install apache-3.0.0 with RHEL and GCC-14 compatibility.

        Key environment variables:
          PATH=/usr/local/bin:$PATH   — pcre-config on EL10 (from setup_pcre.sh)
          CC=gcc-14 / CXX=g++-14     — GCC-14 (symlinks from setup_gcc14.sh)
          CFLAGS includes:
            -Wno-error=incompatible-pointer-types  — apr-util vs libxml2-2.12.x
            -Wno-error=int-conversion              — same
            -Wno-error=implicit-function-declaration — LuaJIT ARM64 (Ubuntu 24.04)
        """
        print(f"\n>>> Installing {self.benchmark_full}...")

        # STEP 0: Pre-flight check (fails fast with clear message vs silent PTS failure)
        self.check_install_prerequisites()

        # STEP 1: Download test profile so install.sh exists before patching
        print("  [INFO] Downloading test profile...")
        subprocess.run(
            ['bash', '-c', f'phoronix-test-suite info {self.benchmark_full}'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

        # STEP 2: Patch install.sh BEFORE batch-install
        self.patch_install_sh()

        # STEP 3: Remove existing PTS installation
        print("  [INFO] Removing existing PTS installation...")
        remove_cmd = f'echo "y" | phoronix-test-suite remove-installed-test "{self.benchmark_full}"'
        subprocess.run(
            ['bash', '-c', remove_cmd],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

        # STEP 4: Clean ~/httpd_ (may be leftover from previous run; install.sh
        #         also does this now via P0, but clean here as a belt-and-suspenders guard)
        httpd_dir = Path.home() / 'httpd_'
        if httpd_dir.exists():
            print("  [INFO] Removing stale ~/httpd_ ...")
            shutil.rmtree(httpd_dir)

        # STEP 5: Build and install
        nproc = os.cpu_count() or 1
        # PATH: /usr/local/bin first so pcre-config is found on EL10
        # CFLAGS: -Wno-error flags for EL10/libxml2 ABI changes + LuaJIT ARM64
        install_cmd = (
            f'PATH="/usr/local/bin:$PATH" '
            f'MAKEFLAGS="-j{nproc}" '
            f'CC=gcc-14 CXX=g++-14 '
            f'CFLAGS="-O3 -march=native -mtune=native '
            f'-Wno-error=incompatible-pointer-types '
            f'-Wno-error=implicit-function-declaration '
            f'-Wno-error=int-conversion" '
            f'CXXFLAGS="-O3 -march=native -mtune=native" '
            f'phoronix-test-suite batch-install {self.benchmark_full}'
        )

        print(f"\n{'>'*80}")
        print("[PTS INSTALL COMMAND]")
        print(f"  {install_cmd}")
        print(f"{'<'*80}\n")

        # Always write install log — PTS typically swallows install.sh stderr,
        # making silent build failures invisible. Log is the only post-mortem evidence.
        install_log_path = os.environ.get("PTS_INSTALL_LOG_PATH", "").strip()
        install_log = (
            Path(install_log_path) if install_log_path
            else (self.results_dir / "install.log")
        )
        self.results_dir.mkdir(parents=True, exist_ok=True)
        print(f"  [INFO] Install log: {install_log}")

        print("  [INFO] Starting installation (this may take a few minutes)...")
        with open(install_log, 'w') as log_f:
            log_f.write(f"[PTS INSTALL COMMAND]\n{install_cmd}\n\n")
            log_f.flush()

            process = subprocess.Popen(
                ['bash', '-c', install_cmd],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1
            )
            install_output = []
            for line in process.stdout:
                print(line, end='')
                log_f.write(line)
                log_f.flush()
                install_output.append(line)

            process.wait()
            returncode = process.returncode

        log_file = install_log
        pts_test_failed, pts_failure_reason = detect_pts_failure_from_log(log_file)

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
            print(f"\n  [ERROR] Installation failed (returncode={returncode})")
            if pts_failure_reason:
                print(f"  [ERROR] PTS failure: {pts_failure_reason}")
            self._show_install_log_tail(install_log)
            sys.exit(1)

        # Verify PTS installed-tests directory
        pts_home = Path.home() / '.phoronix-test-suite'
        installed_dir = pts_home / 'installed-tests' / 'pts' / self.benchmark
        if not installed_dir.exists():
            print(f"  [ERROR] Installation verification failed: {installed_dir} not found")
            sys.exit(1)

        # Verify httpd binary (built to ~/httpd_/bin/httpd, NOT inside installed-tests)
        httpd_binary = httpd_dir / 'bin' / 'httpd'
        if not httpd_dir.exists() or not any(httpd_dir.iterdir()):
            print("  [ERROR] ~/httpd_ is empty or missing after install")
            print("  [INFO] The httpd compilation failed silently inside PTS.")
            print("  [INFO] Common causes: gcc-14 not in PATH, pcre-config missing, APR build error")
            self._show_install_log_tail(install_log)
            sys.exit(1)
        if not httpd_binary.exists():
            print(f"  [ERROR] httpd binary not found: {httpd_binary}")
            print("  [INFO] httpd compilation may have failed silently")
            self._show_install_log_tail(install_log)
            sys.exit(1)

        # Secondary: PTS recognition check (warning only)
        verify_result = subprocess.run(
            ['bash', '-c', f'phoronix-test-suite test-installed {self.benchmark_full}'],
            capture_output=True, text=True
        )
        if verify_result.returncode != 0:
            print("  [WARN] PTS test-installed check failed, but directory exists — continuing")

        print(f"  [OK] Installation verified: {installed_dir}")
        print(f"  [OK] Apache httpd binary: {httpd_binary}")

    # ------------------------------------------------------------------
    # Perf stat parsing
    # ------------------------------------------------------------------

    def parse_perf_stats_and_freq(self, perf_stats_file, freq_start_file, freq_end_file, cpu_list):
        """Parse perf stat output and CPU frequency files."""
        if not self.perf_events or not Path(perf_stats_file).exists():
            return {'note': 'perf monitoring not available', 'cpu_list': cpu_list}

        cpu_ids = [int(c.strip()) for c in cpu_list.split(',')]
        per_cpu_metrics = {cpu_id: {} for cpu_id in cpu_ids}

        try:
            with open(perf_stats_file) as f:
                for line in f:
                    match = re.match(
                        r'CPU(\d+)\s+([\d,.<>a-zA-Z\s]+)\s+([a-zA-Z0-9\-_]+)', line
                    )
                    if match:
                        cpu_num = int(match.group(1))
                        value_str = match.group(2).strip()
                        event = match.group(3)
                        if cpu_num in per_cpu_metrics and '<not supported>' not in value_str:
                            try:
                                value_clean = value_str.split()[0]
                                per_cpu_metrics[cpu_num][event] = float(
                                    value_clean.replace(',', '')
                                )
                            except ValueError:
                                continue
        except FileNotFoundError:
            print(f"  [INFO] Perf stats file not found: {perf_stats_file} (perf likely disabled)")
        except Exception as e:
            print(f"  [WARN] Failed to parse perf stat file: {e}")

        # Parse frequency files
        freq_start = {}
        freq_end = {}
        try:
            with open(freq_start_file) as f:
                for i, line in enumerate(f):
                    if line.strip():
                        freq_start[i] = float(line.strip())
            with open(freq_end_file) as f:
                for i, line in enumerate(f):
                    if line.strip():
                        freq_end[i] = float(line.strip())
        except Exception as e:
            print(f"  [WARN] Failed to parse frequency files: {e}")

        # Build summary
        summary = {
            'avg_frequency_ghz': {},
            'start_frequency_ghz': {},
            'end_frequency_ghz': {},
            'ipc': {},
            'cpu_list': cpu_list,
        }
        for cpu_id in cpu_ids:
            m = per_cpu_metrics[cpu_id]
            cycles = m.get('cycles', 0)
            instructions = m.get('instructions', 0)
            cpu_clock = m.get('cpu-clock', 0)

            if cpu_clock > 0 and cycles > 0:
                summary['avg_frequency_ghz'][str(cpu_id)] = round(
                    cycles / (cpu_clock / 1000.0) / 1e9, 3
                )
            if instructions > 0 and cycles > 0:
                summary['ipc'][str(cpu_id)] = round(instructions / cycles, 3)
            if cpu_id in freq_start:
                summary['start_frequency_ghz'][str(cpu_id)] = round(
                    freq_start[cpu_id] / 1_000_000.0, 3
                )
            if cpu_id in freq_end:
                summary['end_frequency_ghz'][str(cpu_id)] = round(
                    freq_end[cpu_id] / 1_000_000.0, 3
                )

        return summary

    # ------------------------------------------------------------------
    # Benchmark execution
    # ------------------------------------------------------------------

    def run_benchmark(self, num_threads):
        """
        Run apache-3.0.0 benchmark.

        num_threads is always 1 for this single-threaded benchmark.
        Apache server runs on CPU 0 via taskset.
        """
        print(f"\n{'='*80}")
        print(f">>> Running benchmark with {num_threads} thread(s)")
        print(f"{'='*80}")

        self.results_dir.mkdir(parents=True, exist_ok=True)
        log_file = self.results_dir / f"{num_threads}-thread.log"
        stdout_log = self.results_dir / "stdout.log"
        perf_stats_file = self.results_dir / f"{num_threads}-thread_perf_stats.txt"
        freq_start_file = self.results_dir / f"{num_threads}-thread_freq_start.txt"
        freq_end_file = self.results_dir / f"{num_threads}-thread_freq_end.txt"
        perf_summary_file = self.results_dir / f"{num_threads}-thread_perf_summary.json"

        # Single-threaded: pin to CPU 0
        cpu_list = '0'
        pts_base_cmd = f'taskset -c {cpu_list} phoronix-test-suite batch-run {self.benchmark_full}'

        # Remove stale PTS result files to avoid interactive prompts
        sanitized_benchmark = self.benchmark.replace('.', '')
        for rm_name in [
            f'{self.benchmark}-{num_threads}threads',
            f'{sanitized_benchmark}-{num_threads}threads',
        ]:
            subprocess.run(
                ['bash', '-c', f'phoronix-test-suite remove-result {rm_name}'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )

        quick_env = 'FORCE_TIMES_TO_RUN=1 ' if self.quick_mode else ''
        batch_env = (
            f'{quick_env}'
            f'BATCH_MODE=1 SKIP_ALL_PROMPTS=1 DISPLAY_COMPACT_RESULTS=1 '
            f'TEST_RESULTS_NAME={self.benchmark}-{num_threads}threads '
            f'TEST_RESULTS_IDENTIFIER={self.benchmark}-{num_threads}threads '
            f'TEST_RESULTS_DESCRIPTION={self.benchmark}-{num_threads}threads'
        )

        if self.perf_events:
            if self.perf_paranoid <= 0:
                perf_cmd = f'perf stat -e {self.perf_events} -A -a -o {perf_stats_file}'
                perf_mode = "Full (per-CPU + HW counters)"
            else:
                perf_cmd = f'perf stat -e {self.perf_events} -o {perf_stats_file}'
                perf_mode = "Limited (aggregated events only)"
            pts_cmd = f'{batch_env} {perf_cmd} {pts_base_cmd}'
        else:
            pts_cmd = f'{batch_env} {pts_base_cmd}'
            perf_mode = "Disabled"

        print(f"[INFO] Perf mode: {perf_mode}")
        print(f"[INFO] CPU affinity: taskset -c {cpu_list}")
        print(f"\n{'>'*80}")
        print("[PTS BENCHMARK COMMAND]")
        print(f"  {pts_cmd}")
        print(f"{'<'*80}\n")

        # Record start frequency
        print("[INFO] Recording CPU frequency before benchmark...")
        if self.record_cpu_frequency(freq_start_file):
            print("  [OK] Start frequency recorded")
        else:
            print("  [WARN] CPU frequency not available (common on ARM64/cloud VMs)")

        # Execute benchmark
        with open(log_file, 'w') as log_f, open(stdout_log, 'a') as stdout_f:
            stdout_f.write(f"\n{'='*80}\n")
            stdout_f.write(f"[PTS BENCHMARK COMMAND - {num_threads} thread(s)]\n")
            stdout_f.write(f"{pts_cmd}\n")
            stdout_f.write(f"{'='*80}\n\n")
            stdout_f.flush()

            process = subprocess.Popen(
                ['bash', '-c', pts_cmd],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1
            )
            for line in process.stdout:
                print(line, end='')
                log_f.write(line)
                stdout_f.write(line)
                log_f.flush()
                stdout_f.flush()
            process.wait()
            returncode = process.returncode

        # Record end frequency
        print("\n[INFO] Recording CPU frequency after benchmark...")
        if self.record_cpu_frequency(freq_end_file):
            print("  [OK] End frequency recorded")
        else:
            print("  [WARN] CPU frequency not available")

        # Failure detection (returncode + PTS log patterns)
        pts_test_failed = False
        failure_reason = ""
        if log_file.exists():
            log_content = log_file.read_text(errors='ignore')
            failure_patterns = [
                ("Multiple tests are not installed", "PTS test profile not installed"),
                ("The following tests failed", "PTS reported test execution failure"),
                ("quit with a non-zero exit status", "PTS benchmark subprocess failed"),
                ("failed to properly run", "PTS benchmark did not run properly"),
            ]
            for pattern, reason in failure_patterns:
                if pattern.lower() in log_content.lower():
                    pts_test_failed = True
                    failure_reason = reason
                    break

        if returncode == 0 and not pts_test_failed:
            print("\n[OK] Benchmark completed successfully")
            if self.perf_events and perf_stats_file.exists():
                try:
                    perf_summary = self.parse_perf_stats_and_freq(
                        perf_stats_file, freq_start_file, freq_end_file, cpu_list
                    )
                    with open(perf_summary_file, 'w') as f:
                        json.dump(perf_summary, f, indent=2)
                    print(f"  [OK] Perf summary: {perf_summary_file}")
                except Exception as e:
                    print(f"  [WARN] Failed to parse perf stats: {e}")
            return True
        else:
            print("\n[ERROR] Benchmark failed")
            print(f"  Reason: {failure_reason or f'returncode={returncode}'}")
            return False

    # ------------------------------------------------------------------
    # Results export
    # ------------------------------------------------------------------

    def export_results(self):
        """Export benchmark results to CSV and JSON formats."""
        print(f"\n{'='*80}")
        print(">>> Exporting results")
        print(f"{'='*80}")

        pts_results_dir = Path.home() / ".phoronix-test-suite" / "test-results"

        for num_threads in self.thread_list:
            result_name = f"{self.benchmark}-{num_threads}threads"
            # PTS removes dots from directory names
            result_dir_name = result_name.replace('.', '')
            result_dir = pts_results_dir / result_dir_name

            if not result_dir.exists():
                print(f"  [WARN] Result directory not found: {result_dir}")
                print(f"  [INFO] Expected name={result_name}, dir={result_dir_name}")
                continue

            # CSV export
            csv_output = self.results_dir / f"{num_threads}-thread.csv"
            print(f"  [EXPORT] CSV: {csv_output}")
            result = subprocess.run(
                ['phoronix-test-suite', 'result-file-to-csv', result_dir_name],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                home_csv = Path.home() / f"{result_dir_name}.csv"
                if home_csv.exists():
                    shutil.move(str(home_csv), str(csv_output))
                    print(f"  [OK] Saved: {csv_output}")
            else:
                print(f"  [WARN] CSV export failed: {result.stderr.strip()}")

            # JSON export
            json_output = self.results_dir / f"{num_threads}-thread.json"
            print(f"  [EXPORT] JSON: {json_output}")
            result = subprocess.run(
                ['phoronix-test-suite', 'result-file-to-json', result_dir_name],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                home_json = Path.home() / f"{result_dir_name}.json"
                if home_json.exists():
                    shutil.move(str(home_json), str(json_output))
                    print(f"  [OK] Saved: {json_output}")
            else:
                print(f"  [WARN] JSON export failed: {result.stderr.strip()}")

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
                    data = json.loads(json_file.read_text())
                    for result_id, result in data.get('results', {}).items():
                        for system_id, system_result in result.get('results', {}).items():
                            all_results.append({
                                'threads': num_threads,
                                'value': system_result.get('value'),
                                'raw_values': system_result.get('raw_values', []),
                                'test_name': result.get('title'),
                                'description': result.get('description'),
                                'unit': result.get('scale'),
                            })
                except Exception as e:
                    print(f"  [WARN] Failed to parse {json_file}: {e}")

        if not all_results:
            print("[WARN] No results found for summary generation")
            return

        with open(summary_log, 'w') as f:
            f.write("=" * 80 + "\n")
            f.write("Benchmark Summary\n")
            f.write(f"Machine: {self.machine_name}\n")
            f.write(f"Test Category: {self.test_category}\n")
            f.write("Single-threaded: True\n")
            f.write("=" * 80 + "\n\n")
            f.write("=" * 80 + "\n")
            f.write("Summary Table\n")
            f.write("=" * 80 + "\n")
            f.write(f"{'Threads':<10} {'Average':<15} {'Unit':<20}\n")
            f.write("-" * 80 + "\n")
            for result in all_results:
                val_str = (
                    f"{result['value']:<15.2f}" if result['value'] is not None
                    else "FAILED         "
                )
                f.write(f"{result['threads']:<10} {val_str} {result['unit'] or '':<20}\n")

        print(f"[OK] Summary log saved: {summary_log}")

        summary_data = {
            "benchmark": self.benchmark,
            "test_category": self.test_category,
            "machine": self.machine_name,
            "vcpu_count": self.vcpu_count,
            "single_threaded": True,
            "results": all_results,
        }
        with open(summary_json_file, 'w') as f:
            json.dump(summary_data, f, indent=2)

        print(f"[OK] Summary JSON saved: {summary_json_file}")

    # ------------------------------------------------------------------
    # Main flow
    # ------------------------------------------------------------------

    def run(self):
        """Main execution method."""
        print(f"{'='*80}")
        print("Apache HTTP Server Benchmark Runner")
        print(f"{'='*80}")
        print(f"[INFO] Machine:       {self.machine_name}")
        print(f"[INFO] OS:            {self.os_name}")
        print(f"[INFO] vCPU count:    {self.vcpu_count}")
        print(f"[INFO] Test category: {self.test_category}")
        print("[INFO] Thread mode:   Single-threaded (TH_scaling=single-threaded)")
        print(f"[INFO] Thread list:   {self.thread_list}")
        print(f"[INFO] Results dir:   {self.results_dir}")
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
            print(f"  [INFO] Cleaned existing {prefix} results")

        # Install check
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
                "[WARN] Existing install directory found but PTS does not recognize it. "
                "Treating as broken install and reinstalling."
            )

        if not already_installed:
            self.install_benchmark()
        else:
            print(f"[INFO] Benchmark already installed, skipping: {self.benchmark_full}")

        # Run benchmark
        failed = []
        for num_threads in self.thread_list:
            if not self.run_benchmark(num_threads):
                failed.append(num_threads)

        # Export and summarize
        self.export_results()
        self.generate_summary()
        cleanup_pts_artifacts(self.benchmark)

        # ── Runner Output Protocol ────────────────────────────────────────────
        # cloud_exec_para.py scans stdout for "Failed: N" (N > 0 = error).
        print(f"\n{'='*80}")
        print("Benchmark Summary")
        print(f"{'='*80}")
        print(f"Total tests:  {len(self.thread_list)}")
        print(f"Successful:   {len(self.thread_list) - len(failed)}")
        print(f"Failed:       {len(failed)}")
        if failed:
            print(f"Failed thread counts: {failed}")
        print(f"{'='*80}")
        # ─────────────────────────────────────────────────────────────────────

        return len(failed) == 0


def main():
    parser = argparse.ArgumentParser(
        description='Apache HTTP Server Benchmark Runner (single-threaded)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s           # Run benchmark (single-threaded, always uses 1 thread)
  %(prog)s --quick   # Quick mode: FORCE_TIMES_TO_RUN=1
        """
    )
    parser.add_argument(
        'threads_pos', nargs='?', type=int,
        help='Thread count argument (ignored — this is a single-threaded benchmark)'
    )
    parser.add_argument(
        '--threads', type=int,
        help='Thread count argument (ignored — this is a single-threaded benchmark)'
    )
    parser.add_argument(
        '--quick', action='store_true',
        help='Quick mode: run tests once (FORCE_TIMES_TO_RUN=1) for development'
    )

    args = parser.parse_args()
    threads = args.threads if args.threads is not None else args.threads_pos

    if args.quick:
        print("[INFO] Quick mode enabled: FORCE_TIMES_TO_RUN=1")
    if threads is not None:
        print(f"[INFO] --threads={threads} specified but ignored (single-threaded benchmark)")

    runner = ApacheRunner(threads_arg=threads, quick_mode=args.quick)
    success = runner.run()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
