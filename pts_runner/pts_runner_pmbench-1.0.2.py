#!/usr/bin/env python3
"""
PTS Runner for pmbench-1.0.2

System Dependencies (from phoronix-test-suite info):
- Software Dependencies:
  * C/C++ Compiler Toolchain
  * UUID
  * Libxml2
- Estimated Install Time: 2 Seconds
- Environment Size: 3 MB
- Test Type: Memory
- Supported Platforms: Linux

Test Characteristics:
- Multi-threaded: Yes (supports concurrent worker threads)
- Honors CFLAGS/CXXFLAGS: Yes
- Notable Instructions: N/A
- THFix_in_compile: false - Thread count NOT fixed at compile time
- THChange_at_runtime: true - Runtime thread configuration via -j option
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

    def download_from_xml(self, benchmark_name, threshold_mb=96):
        """
        Parse downloads.xml for the benchmark and download large files.
        
        Args:
            benchmark_name: Full benchmark name (e.g., "pts/x265-1.5.0")
            threshold_mb: Size threshold in MB to trigger aria2c (default: 256MB)
        """
        if not self.aria2_available:
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
        try:
            import xml.etree.ElementTree as ET
            tree = ET.parse(profile_path)
            root = tree.getroot()
            
            # Find all Package elements
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
                
                # If size not in XML, try to get it from network (fallback)
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
            # -s: Silent, -I: Header only, -L: Follow redirects
            cmd = ['curl', '-s', '-I', '-L', url]
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                return -1
                
            # Parse Content-Length
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
            print(f"  [WARN] aria2c download failed, falling back to PTS default")
            # Clean up partial download
            if target_path.exists():
                target_path.unlink()
            return False

class PmbenchRunner:
    def __init__(self, threads_arg=None, quick_mode=False, dry_run=False, force_arm64=False):
        """
        Initialize pmbench (paging/virtual memory benchmark) runner.

        Args:
            threads_arg: Thread count argument (None for scaling mode, int for fixed mode)
            quick_mode: If True, run tests once (FORCE_TIMES_TO_RUN=1) for development
        """
        self.benchmark = "pmbench-1.0.2"
        self.benchmark_full = f"pts/{self.benchmark}"
        self.test_category = "Memory Access"
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
        self.results_dir.mkdir(parents=True, exist_ok=True)

        # Quick mode for development
        self.quick_mode = quick_mode
        self.dry_run = dry_run
        self.force_arm64 = force_arm64
        # Debug dump is disabled by default; enable via hardcoded flag for this runner only.
        self.debug_dump = False
        ENABLE_DEBUG_DUMP = True
        if ENABLE_DEBUG_DUMP:
            self.debug_dump = True

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

    def _copy_debug_dump(self, stage: str = "runtime"):
        if not self.debug_dump:
            return
        self.results_dir.mkdir(parents=True, exist_ok=True)
        tmp_log = Path("/tmp/pts_runner_pmbench.log")
        if tmp_log.exists():
            try:
                dest = self.results_dir / "pts_runner_pmbench.log"
                shutil.copy2(tmp_log, dest)
                print(f"  [INFO] Copied {tmp_log} -> {dest} ({stage})")
            except Exception as e:
                print(f"  [WARN] Failed to copy {tmp_log}: {e}")

    def _collect_pts_diagnostics(self, stage: str = "install-failed"):
        if not self.debug_dump:
            return
        diag_dir = self.results_dir / "pts_diagnostics"
        diag_dir.mkdir(parents=True, exist_ok=True)
        pts_home = Path.home() / ".phoronix-test-suite"
        profile_dir = pts_home / "test-profiles" / "pts" / self.benchmark
        installed_dir = pts_home / "installed-tests" / "pts" / self.benchmark
        core_log = pts_home / "pts-core.log"

        def _copy_tree(src: Path, dest: Path):
            if not src.exists():
                return
            try:
                shutil.copytree(src, dest, dirs_exist_ok=True)
                print(f"  [INFO] Copied {src} -> {dest}")
            except Exception as e:
                print(f"  [WARN] Failed to copy {src}: {e}")

        _copy_tree(profile_dir, diag_dir / "test-profiles")
        _copy_tree(installed_dir, diag_dir / "installed-tests")
        if core_log.exists():
            try:
                shutil.copy2(core_log, diag_dir / "pts-core.log")
                print(f"  [INFO] Copied {core_log} -> {diag_dir / 'pts-core.log'}")
            except Exception as e:
                print(f"  [WARN] Failed to copy {core_log}: {e}")

        try:
            (diag_dir / "ls_profile.txt").write_text(
                subprocess.run(
                    ["bash", "-c", f'ls -la "{profile_dir}"'],
                    capture_output=True,
                    text=True,
                ).stdout
            )
            (diag_dir / "ls_installed.txt").write_text(
                subprocess.run(
                    ["bash", "-c", f'ls -la "{installed_dir}"'],
                    capture_output=True,
                    text=True,
                ).stdout
            )
            (diag_dir / "find_pmbench.txt").write_text(
                subprocess.run(
                    ["bash", "-c", f'find "{pts_home}" -type f -name "pmbench" -print'],
                    capture_output=True,
                    text=True,
                ).stdout
            )
        except Exception as e:
            print(f"  [WARN] Failed to write diagnostics listings: {e}")

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
        installed_dir = pts_home / 'installed-tests' / 'pts' / self.benchmark

        if installed_dir.exists():
            print(f"  [CLEAN] Removing installed test: {installed_dir}")
            shutil.rmtree(installed_dir)

        print("  [OK] PTS cache cleaned")

    def patch_install_script(self):
        """
        Patch PTS install.sh to remove x86-only flags (e.g., -m64) on ARM64.
        """
        arch = os.uname().machine
        if not (self.force_arm64 or arch in {"aarch64", "arm64"}):
            return True

        # Ensure test profile exists locally
        subprocess.run(
            ['phoronix-test-suite', 'info', self.benchmark_full],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        install_sh = (
            Path.home()
            / ".phoronix-test-suite"
            / "test-profiles"
            / "pts"
            / self.benchmark
            / "install.sh"
        )

        if not install_sh.exists():
            print(f"  [WARN] install.sh not found for patching: {install_sh}")
            return False

        try:
            patch_marker = "# ARM64 workaround: force rebuild to avoid stale x86 objects"

            def _has_make_line(text):
                return re.search(r'^\s*(?:g?make)\b', text, flags=re.MULTILINE) is not None

            def _refresh_profile():
                profile_dir = install_sh.parent
                if profile_dir.exists():
                    shutil.rmtree(profile_dir)
                subprocess.run(
                    ['phoronix-test-suite', 'info', self.benchmark_full],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )

            # Always start from a pristine copy to avoid cumulative corruption
            orig_path = install_sh.with_suffix(install_sh.suffix + ".orig")
            content = None

            if orig_path.exists():
                content = orig_path.read_text()
                if not _has_make_line(content):
                    _refresh_profile()
                    content = install_sh.read_text() if install_sh.exists() else content
                    orig_path.write_text(content)
            else:
                _refresh_profile()
                content = install_sh.read_text() if install_sh.exists() else None
                if content:
                    orig_path.write_text(content)

            if not content:
                raise RuntimeError("Failed to load pristine install.sh")

            lines = content.splitlines()
            new_lines = []
            injected = False
            for line in lines:
                if not injected and re.match(r'^\s*(?:g?make)\b', line):
                    rebuilt = line
                    if "-B" not in line:
                        rebuilt = re.sub(r'(^\s*(?:g?make)\b)', r'\1 -B', line)
                    lib_block = [
                        'XML2_LIBS=""',
                        'XML2_CFLAGS=""',
                        'if command -v pkg-config >/dev/null 2>&1; then',
                        '  XML2_LIBS="$(pkg-config --libs libxml-2.0 2>/dev/null)"',
                        '  XML2_CFLAGS="$(pkg-config --cflags libxml-2.0 2>/dev/null)"',
                        'fi',
                        'LIBS_EXTRA="-lxml2 -lm -luuid -pthread"',
                        'export LIBS="$XML2_LIBS $LIBS_EXTRA"',
                        'export LDLIBS="$XML2_LIBS $LIBS_EXTRA"',
                        'export LDFLAGS="${LDFLAGS} $XML2_LIBS $LIBS_EXTRA"',
                        'export CPPFLAGS="${CPPFLAGS} -I/usr/include/libxml2"',
                        'export CFLAGS="${CFLAGS} $XML2_CFLAGS -I/usr/include/libxml2"',
                        'echo "[DEBUG] XML2_CFLAGS=${XML2_CFLAGS}"',
                        'echo "[DEBUG] CPPFLAGS=${CPPFLAGS}"',
                        'echo "[DEBUG] CFLAGS=${CFLAGS}"',
                        'if command -v gcc >/dev/null 2>&1; then',
                        '  echo "[DEBUG] gcc include search paths:"',
                        '  echo | gcc -E -x c - -v 2>&1 | sed -n \'/#include <...> search starts here:/,/End of search list./p\'',
                        'fi',
                        'if [ -f Makefile ]; then',
                        "  sed -i -E 's/^(LIBS[[:space:]]*[:+]?=)/LIBS ?=/' Makefile || true",
                        "  if ! grep -q 'PTS_LIBS_FIX' Makefile; then",
                        "    echo '' >> Makefile",
                        "    echo '# PTS_LIBS_FIX' >> Makefile",
                        "    echo 'LIBS += -lxml2 -lm -luuid -pthread' >> Makefile",
                        "    echo 'LDLIBS += -lxml2 -lm -luuid -pthread' >> Makefile",
                        "    echo 'LDFLAGS += -lxml2 -lm -luuid -pthread' >> Makefile",
                        "  fi",
                        "  sed -i -E '/-o[[:space:]]+pmbench/ { /\\$\\(LIBS\\)/! s/$/ $(LIBS)/ }' Makefile || true",
                        "  sed -i -E '/-o[[:space:]]+xmlgen/ { /\\$\\(LIBS\\)/! s/$/ $(LIBS)/ }' Makefile || true",
                        "fi",
                        'if [ -f rdtsc.h ]; then',
                        "  cat > rdtsc.h <<'EOF'",
                        '#ifndef PTS_ARM64_RDTSC_H',
                        '#define PTS_ARM64_RDTSC_H',
                        '#include <stdint.h>',
                        '#if defined(__aarch64__)',
                        'static inline uint64_t rdtsc(void) {',
                        '  uint64_t v;',
                        '  asm volatile("mrs %0, cntvct_el0" : "=r"(v));',
                        '  return v;',
                        '}',
                        'static inline uint64_t rdtscp(void) {',
                        '  uint64_t v;',
                        '  asm volatile("mrs %0, cntvct_el0" : "=r"(v));',
                        '  return v;',
                        '}',
                        '#elif defined(__x86_64__) || defined(__i386__)',
                        'static inline uint64_t rdtsc(void) {',
                        '  uint32_t lo, hi;',
                        '  asm volatile("rdtsc" : "=a"(lo), "=d"(hi));',
                        '  return ((uint64_t)hi << 32) | lo;',
                        '}',
                        'static inline uint64_t rdtscp(void) {',
                        '  uint32_t lo, hi;',
                        '  asm volatile("rdtscp" : "=a"(lo), "=d"(hi) :: "rcx");',
                        '  return ((uint64_t)hi << 32) | lo;',
                        '}',
                        '#else',
                        'static inline uint64_t rdtsc(void) { return 0; }',
                        'static inline uint64_t rdtscp(void) { return 0; }',
                        '#endif',
                        '#endif',
                        'EOF',
                        "  echo \"[DEBUG] rdtsc.h patched for ARM64\"",
                        "fi",
                        'echo "[DEBUG] LIBS=$LIBS"',
                        'echo "[DEBUG] LDLIBS=$LDLIBS"',
                        'echo "[DEBUG] LDFLAGS=$LDFLAGS"',
                        'if [ -f Makefile ]; then',
                        '  echo "[DEBUG] Makefile LIBS/LDLIBS/LDFLAGS:"',
                        "  grep -nE '^(LIBS|LDLIBS|LDFLAGS)\\s*=' Makefile || true",
                        "  echo \"[DEBUG] make line:\",",
                        "  grep -nE '^\\s*(g?make)\\b' Makefile || true",
                        '  echo "[DEBUG] Makefile link lines (pmbench/xmlgen):"',
                        "  grep -nE '(pmbench|xmlgen)' Makefile || true",
                        "fi",
                    ]
                    make_with_libs = f'{rebuilt} LIBS="$LIBS" LDLIBS="$LDLIBS" LDFLAGS="$LDFLAGS"'
                    block = [
                        patch_marker,
                        'if [ "$(uname -m)" = "aarch64" ] || [ "$(uname -m)" = "arm64" ]; then',
                        '  find . -type f \\( -name "*.o" -o -name "*.a" -o -name "*.obj" \\) -delete 2>/dev/null || true',
                        *[f"  {l}" for l in lib_block],
                        f"  {make_with_libs}",
                        "else",
                        f"  {line}",
                        "fi",
                    ]
                    new_lines.extend(block)
                    injected = True
                    continue
                new_lines.append(line)

            if not injected:
                print("  [WARN] No make line found to patch in install.sh")

            content = "\n".join(new_lines)
            install_sh.write_text(content)
            print("  [OK] Patched install.sh: force rebuild on ARM64")
            if self.dry_run:
                self.print_install_script_snippet(install_sh)
            return True
        except Exception as e:
            print(f"  [ERROR] Failed to patch install.sh: {e}")
            return False

    def print_install_script_snippet(self, install_sh, head_lines=80):
        print(f"\n>>> install.sh snippet: {install_sh}")
        try:
            lines = install_sh.read_text().splitlines()
        except Exception as e:
            print(f"  [ERROR] Failed to read install.sh: {e}")
            return

        for idx, line in enumerate(lines[:head_lines], start=1):
            print(f"{idx:4d}: {line}")

        keywords = ("ARM64 workaround", "find . -type f", "sed -i", "make", "gmake")
        print("\n>>> install.sh matches:")
        for idx, line in enumerate(lines, start=1):
            if any(k in line for k in keywords):
                print(f"{idx:4d}: {line}")

    def prepare_compiler_wrapper(self):
        """
        Create gcc/g++ wrappers that strip -m64 on ARM64.
        This guards against Makefiles that hardcode gcc without honoring CC/CXX.
        """
        arch = os.uname().machine
        if not (self.force_arm64 or arch in {"aarch64", "arm64"}):
            return None

        wrapper_dir = Path("/tmp/pts_ccwrap")
        wrapper_dir.mkdir(parents=True, exist_ok=True)

        wrapper_script = """#!/bin/sh
WRAP_NAME="__WRAP_NAME__"
REAL_CC="$1"
shift
if [ ! -x "$REAL_CC" ]; then
  # Fallback if the expected compiler path is missing
  if echo "$WRAP_NAME" | grep -q '\\+\\+'; then
    REAL_CC="$(command -v g++ 2>/dev/null || command -v c++ 2>/dev/null || command -v gcc 2>/dev/null)"
  else
    REAL_CC="$(command -v gcc 2>/dev/null || command -v cc 2>/dev/null)"
  fi
fi
ARGS=""
HAS_O=0
HAS_C=0
OUT_FILE=""
for a in "$@"; do
  if [ "$a" = "-o" ]; then
    HAS_O=1
  fi
  if [ "$a" = "-c" ]; then
    HAS_C=1
  fi
  if [ "$HAS_O" = "1" ] && [ -z "$OUT_FILE" ] && [ "$a" != "-o" ]; then
    OUT_FILE="$a"
  fi
  if [ "$a" != "-m64" ]; then
    ARGS="$ARGS \"$a\""
  fi
done
if [ "$HAS_C" = "0" ] && [ -n "$OUT_FILE" ]; then
  case "$OUT_FILE" in
    *.o|*.obj) ARGS="$ARGS -c" ;;
  esac
fi
if [ "$HAS_O" = "1" ] && [ "$HAS_C" = "0" ] && [ -n "$PTS_EXTRA_LINK_LIBS" ]; then
  ARGS="$ARGS $PTS_EXTRA_LINK_LIBS"
fi
eval "$REAL_CC" $ARGS
"""

        def _add_wrapper(name, real):
            target = wrapper_dir / name
            content = wrapper_script.replace("REAL_CC=\"$1\"", f"REAL_CC=\"{real}\"")
            content = content.replace("__WRAP_NAME__", name)
            target.write_text(content)
            target.chmod(0o755)

        # Core compiler names
        for name, real in [
            ("gcc", "/usr/bin/gcc"),
            ("g++", "/usr/bin/g++"),
            ("clang", "/usr/bin/clang"),
            ("clang++", "/usr/bin/clang++"),
        ]:
            if Path(real).exists():
                _add_wrapper(name, real)

        # Versioned compiler binaries (gcc-*, g++-*, clang-*, clang++-*)
        for pattern in ("gcc-*", "g++-*", "clang-*", "clang++-*"):
            for real_path in Path("/usr/bin").glob(pattern):
                if real_path.is_file():
                    _add_wrapper(real_path.name, str(real_path))

        print("  [OK] Compiler wrapper enabled: stripping -m64 on ARM64")
        return wrapper_dir

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

    def install_benchmark(self):
        """Install benchmark with error detection and verification."""
        # [Pattern 5] Pre-download large files from downloads.xml (Size > 256MB)
        print(f"\n>>> Checking for large files to pre-seed...")
        downloader = PreSeedDownloader()
        downloader.download_from_xml(self.benchmark_full, threshold_mb=96)

        print(f"\n>>> Installing {self.benchmark_full}...")
        self.patch_install_script()
        # Emit debug info to runner log to ensure it shows up in cloud_exec tail
        if os.environ.get("PTS_DEBUG_HEADERS", "").strip().lower() in {"1", "true", "yes"}:
            print("\n>>> [DEBUG] Compiler include search paths (gcc -v):")
            try:
                debug_out = subprocess.run(
                    "echo | gcc -E -x c - -v",
                    shell=True,
                    capture_output=True,
                    text=True
                )
                print(debug_out.stderr.strip())
            except Exception as e:
                print(f"[DEBUG] Failed to dump gcc include paths: {e}")
            print("\n>>> [DEBUG] install.sh snippet (first 120 lines):")
            try:
                self.print_install_script_snippet(
                    Path.home() / ".phoronix-test-suite" / "test-profiles" / "pts" / self.benchmark / "install.sh",
                    head_lines=120
                )
            except Exception as e:
                print(f"[DEBUG] Failed to dump install.sh snippet: {e}")
        if self.dry_run:
            print("\n>>> Dry run enabled: skipping installation.")
            return True

        print(f"  [INFO] Removing existing installation...")
        remove_cmd = f'echo "y" | phoronix-test-suite remove-installed-test "{self.benchmark_full}"'
        subprocess.run(['bash', '-c', remove_cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        nproc = os.cpu_count() or 1
        env_cc = os.environ.get("CC", "").strip()
        env_cxx = os.environ.get("CXX", "").strip()

        def _pick_compiler(primary, fallback):
            if primary and shutil.which(primary):
                return primary
            if shutil.which(fallback):
                return fallback
            return primary or fallback

        cc = _pick_compiler(env_cc, "gcc-14")
        cxx = _pick_compiler(env_cxx, "g++-14")
        if not shutil.which(cc):
            cc = _pick_compiler(env_cc, "gcc")
        if not shutil.which(cxx):
            cxx = _pick_compiler(env_cxx, "g++")

        xml2_cflags = ""
        try:
            if shutil.which("pkg-config"):
                xml2_cflags = subprocess.run(
                    ["pkg-config", "--cflags", "libxml-2.0"],
                    capture_output=True,
                    text=True,
                    check=False
                ).stdout.strip()
        except Exception:
            xml2_cflags = ""
        include_dir = "/usr/include/libxml2"
        if Path(include_dir).exists() and f"-I{include_dir}" not in xml2_cflags:
            xml2_cflags = f"{xml2_cflags} -I{include_dir}".strip()

        wrapper_dir = self.prepare_compiler_wrapper()
        path_prefix = f'PATH="{wrapper_dir}:$PATH" ' if wrapper_dir else ""
        cflags_value = f'-O3 -march=native -mtune=native {xml2_cflags}'.strip()
        cxxflags_value = f'-O3 -march=native -mtune=native {xml2_cflags}'.strip()
        cppflags_prefix = f'CPPFLAGS="{xml2_cflags}" ' if xml2_cflags else ""
        include_path_prefix = ""
        if Path(include_dir).exists():
            def _merge_env_path(var_name, value):
                current = os.environ.get(var_name, "").strip()
                if current:
                    parts = current.split(":")
                    if value in parts:
                        return current
                    return f"{value}:{current}"
                return value

            cpath = _merge_env_path("CPATH", include_dir)
            c_include = _merge_env_path("C_INCLUDE_PATH", include_dir)
            cplus_include = _merge_env_path("CPLUS_INCLUDE_PATH", include_dir)
            include_path_prefix = (
                f'CPATH="{cpath}" C_INCLUDE_PATH="{c_include}" CPLUS_INCLUDE_PATH="{cplus_include}" '
            )
        extra_link_libs = "-lxml2 -lm -luuid -pthread"
        install_cmd = (
            f'{path_prefix}{include_path_prefix}{cppflags_prefix}PTS_DEBUG=1 PTS_EXTRA_LINK_LIBS="{extra_link_libs}" '
            f'MAKEFLAGS="-j{nproc} V=1" CC={cc} CXX={cxx} '
            f'CFLAGS="{cflags_value}" '
            f'CXXFLAGS="{cxxflags_value}" '
            f'phoronix-test-suite batch-install {self.benchmark_full}'
        )

        print(f"\n{'>'*80}")
        print(f"[PTS INSTALL COMMAND]")
        print(f"  {install_cmd}")
        print(f"{'<'*80}\n")

        # Execute install command with real-time output streaming (stdout+stderr)
        print(f"  Running installation...")
        install_log_path = os.environ.get("PTS_INSTALL_LOG_PATH", "").strip()
        use_install_log = True
        install_log = Path(install_log_path) if install_log_path else (self.results_dir / "install.log")

        extra_log_paths = []
        if self.debug_dump:
            extra_log_paths = [
                self.results_dir / "pts_runner_pmbench.log",
                Path("/tmp/pts_runner_pmbench.log"),
            ]

        def _run_install(log_f=None):
            extra_logs = []
            for path in extra_log_paths:
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    extra_logs.append(open(path, "a"))
                except Exception:
                    pass

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
                for extra_log in extra_logs:
                    try:
                        extra_log.write(line)
                        extra_log.flush()
                    except Exception:
                        pass
                install_output.append(line)

            process.wait()
            for extra_log in extra_logs:
                try:
                    extra_log.close()
                except Exception:
                    pass
            return process.returncode, install_output

        if use_install_log:
            with open(install_log, 'w') as log_f:
                returncode, install_output = _run_install(log_f)
        else:
            returncode, install_output = _run_install()
        self._copy_debug_dump("after-install")

        def _collect_install_failed_log():
            install_failed_log = (
                Path.home()
                / ".phoronix-test-suite"
                / "installed-tests"
                / "pts"
                / self.benchmark
                / "install-failed.log"
            )
            if install_failed_log.exists():
                try:
                    dest_log = self.results_dir / "install-failed.log"
                    shutil.copy2(install_failed_log, dest_log)
                    print(f"  [INFO] Copied install-failed.log -> {dest_log}")
                except Exception as e:
                    print(f"  [WARN] Failed to copy install-failed.log: {e}")
            return install_failed_log

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
            # Attempt to surface PTS install-failed log for easier debugging
            install_failed_log = _collect_install_failed_log()
            if install_failed_log.exists():
                try:
                    print(f"  [INFO] install-failed.log (tail):")
                    tail = install_failed_log.read_text().splitlines()[-50:]
                    for line in tail:
                        print(f"    {line}")
                except Exception as e:
                    print(f"  [WARN] Failed to read install-failed.log: {e}")
            self._copy_debug_dump("install-failed")
            self._collect_pts_diagnostics("install-failed")
            sys.exit(1)

        # Verify installation by checking if directory exists
        pts_home = Path.home() / '.phoronix-test-suite'
        installed_dir = pts_home / 'installed-tests' / 'pts' / self.benchmark

        if not installed_dir.exists():
            print(f"  [ERROR] Installation verification failed")
            print(f"  [ERROR] Expected directory not found: {installed_dir}")
            print(f"  [INFO] Installation may have failed silently")
            print(f"  [INFO] Try manually installing: phoronix-test-suite install {self.benchmark_full}")
            _collect_install_failed_log()
            self._copy_debug_dump("install-verify-failed")
            self._collect_pts_diagnostics("install-verify-failed")
            sys.exit(1)
        pmbench_bin = installed_dir / "pmbench"
        if not pmbench_bin.exists():
            print(f"  [ERROR] Installation verification failed")
            print(f"  [ERROR] Expected test executable not found: {pmbench_bin}")
            print(f"  [INFO] Installation may have failed silently")
            _collect_install_failed_log()
            self._copy_debug_dump("install-verify-failed")
            self._collect_pts_diagnostics("install-verify-failed")
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
        try:
            print("  [INFO] Locating pmbench binary...")
            find_cmd = f'find "{installed_dir}" -type f -name "pmbench" -print'
            find_result = subprocess.run(
                ['bash', '-c', find_cmd],
                capture_output=True,
                text=True
            )
            if find_result.stdout.strip():
                for line in find_result.stdout.strip().splitlines():
                    print(f"  [INFO] pmbench binary: {line}")
            else:
                print("  [WARN] No pmbench binary found under installed dir")
            if find_result.stderr.strip():
                print(f"  [WARN] find stderr: {find_result.stderr.strip()}")
        except Exception as e:
            print(f"  [WARN] Failed to locate pmbench binary: {e}")
        self._copy_debug_dump("install-success")

    def parse_perf_stats_and_freq(self, perf_stats_file, freq_start_file, freq_end_file, cpu_list):
        """Parse perf stat output and CPU frequency files."""
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
            "elapsed_time_sec": 0.0
        }

        # Parse start frequencies
        if freq_start_file.exists():
            with open(freq_start_file, 'r') as f:
                freq_lines = f.readlines()
            for idx, cpu_id in enumerate(cpu_ids):
                if idx < len(freq_lines):
                    freq_khz = float(freq_lines[idx].strip())
                    perf_summary["start_frequency_ghz"][str(cpu_id)] = round(freq_khz / 1e6, 3)

        # Parse end frequencies
        if freq_end_file.exists():
            with open(freq_end_file, 'r') as f:
                freq_lines = f.readlines()
            for idx, cpu_id in enumerate(cpu_ids):
                if idx < len(freq_lines):
                    freq_khz = float(freq_lines[idx].strip())
                    perf_summary["end_frequency_ghz"][str(cpu_id)] = round(freq_khz / 1e6, 3)

        # Parse perf stats
        if perf_stats_file.exists():
            with open(perf_stats_file, 'r') as f:
                perf_data = f.read()

            per_cpu_cycles = {}
            per_cpu_instructions = {}
            per_cpu_clock = {}

            for cpu_id in cpu_ids:
                cycles_match = re.search(rf'CPU{cpu_id}\s+(\d+(?:,\d+)*)\s+cycles', perf_data)
                if cycles_match:
                    per_cpu_cycles[str(cpu_id)] = int(cycles_match.group(1).replace(',', ''))

                instr_match = re.search(rf'CPU{cpu_id}\s+(\d+(?:,\d+)*)\s+instructions', perf_data)
                if instr_match:
                    per_cpu_instructions[str(cpu_id)] = int(instr_match.group(1).replace(',', ''))

                clock_match = re.search(rf'CPU{cpu_id}\s+([\d,]+(?:\.\d+)?)\s+msec\s+cpu-clock', perf_data)
                if clock_match:
                    per_cpu_clock[str(cpu_id)] = float(clock_match.group(1).replace(',', ''))

            # Calculate IPC and average frequency
            for cpu_id_str in per_cpu_cycles.keys():
                cycles = per_cpu_cycles.get(cpu_id_str, 0)
                instructions = per_cpu_instructions.get(cpu_id_str, 0)
                clock_ms = per_cpu_clock.get(cpu_id_str, 0)

                perf_summary["total_cycles"][cpu_id_str] = cycles
                perf_summary["total_instructions"][cpu_id_str] = instructions

                if cycles > 0:
                    perf_summary["ipc"][cpu_id_str] = round(instructions / cycles, 2)

                if clock_ms > 0 and cycles > 0:
                    avg_freq_ghz = (cycles / (clock_ms / 1000)) / 1e9
                    perf_summary["avg_frequency_ghz"][cpu_id_str] = round(avg_freq_ghz, 3)

            # Parse elapsed time
            elapsed_match = re.search(r'([\d,]+(?:\.\d+)?)\s+seconds time elapsed', perf_data)
            if elapsed_match:
                perf_summary["elapsed_time_sec"] = float(elapsed_match.group(1).replace(',', ''))

        print(f"  [OK] Perf stats parsed successfully")
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

        perf_stats_file = thread_dir / f"{num_threads}-thread_perf_stats.txt"
        freq_start_file = thread_dir / f"{num_threads}-thread_freq_start.txt"
        freq_end_file = thread_dir / f"{num_threads}-thread_freq_end.txt"
        perf_summary_file = thread_dir / f"{num_threads}-thread_perf_summary.json"

        log_file = self.results_dir / f"{num_threads}-thread.log"
        stdout_log = self.results_dir / "stdout.log"

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

        # Wrap with perf stat (environment variables BEFORE perf stat)
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

                # Save perf summary
                with open(perf_summary_file, 'w') as f:
                    json.dump(perf_summary, f, indent=2)
                print(f"  [OK] Perf summary saved to {perf_summary_file}")
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

        # Clean and install
        self.clean_pts_cache()
        self.install_benchmark()
        if self.dry_run:
            print("\n>>> Dry run enabled: skipping benchmark run.")
            return True

        # Run for each thread count
        failed = []
        for num_threads in self.thread_list:
            if not self.run_benchmark(num_threads):
                failed.append(num_threads)

        # Export results to CSV and JSON
        self.export_results()

        # Generate summary
        self.generate_summary()

        print(f"\n{'='*80}")
        print(f"Benchmark Summary")
        print(f"{'='*80}")
        print(f"Total tests: {len(self.thread_list)}")
        print(f"Successful: {len(self.thread_list) - len(failed)}")
        print(f"Failed: {len(failed)}")
        print(f"{'='*80}")

        return len(failed) == 0

    def export_results(self):
        """Export benchmark results to CSV and JSON formats."""
        print(f"\n{'='*80}")
        print(f">>> Exporting benchmark results")
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

        print(f"\n[OK] Export completed")

    def generate_summary(self):
        """Generate summary.log and summary.json from all thread results."""
        print(f"\n{'='*80}")
        print(f">>> Generating summary")
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
            f.write("="*80 + "\\n")
            f.write(f"Benchmark Summary: {self.benchmark}\\n")
            f.write(f"Machine: {self.machine_name}\\n")
            f.write(f"Test Category: {self.test_category}\\n")
            f.write("="*80 + "\\n\\n")

            for result in all_results:
                f.write(f"Threads: {result['threads']}\\n")
                f.write(f"  Test: {result['test_name']}\\n")
                f.write(f"  Description: {result['description']}\\n")
                
                # Check for None to avoid f-string crash
                val_str = f"{result['value']:.2f}" if result['value'] is not None else "FAILED"
                f.write(f"  Average: {val_str} {result['unit']}\\n")
                    
                # Handle raw values safely
                raw_vals = result.get('raw_values')
                if raw_vals:
                    val_str = ', '.join([f'{v:.2f}' for v in raw_vals if v is not None])
                    f.write(f"  Raw values: {val_str}\\n")
                else:
                    f.write(f"  Raw values: N/A\\n")
                    
                f.write("\\n")

            f.write("="*80 + "\\n")
            f.write("Summary Table\\n")
            f.write("="*80 + "\\n")
            f.write(f"{'Threads':<10} {'Average':<15} {'Unit':<20}\\n")
            f.write("-"*80 + "\\n")
            for result in all_results:
                val_str = f"{result['value']:.2f}" if result['value'] is not None else "None"
                f.write(f"{result['threads']:<10} {val_str:<15} {result['unit']:<20}\\n")

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


def main():
    parser = argparse.ArgumentParser(
        description="pmbench Linux Paging/Virtual Memory Benchmark Runner",
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
        '--dry-run',
        action='store_true',
        help='Dry run: patch install.sh and print snippet, but skip install/run'
    )
    parser.add_argument(
        '--force-arm64',
        action='store_true',
        help='Force ARM64 patching on non-ARM64 hosts (for local verification)'
    )

    args = parser.parse_args()

    if args.quick:
        print("[INFO] Quick mode enabled: FORCE_TIMES_TO_RUN=1")
        print("[INFO] Tests will run once instead of 3+ times (60-70%% time reduction)")

    # Resolve threads argument (prioritize --threads if both provided)
    threads = args.threads if args.threads is not None else args.threads_pos

    runner = PmbenchRunner(
        threads_arg=threads,
        quick_mode=args.quick,
        dry_run=args.dry_run,
        force_arm64=args.force_arm64
    )
    success = runner.run()

    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
