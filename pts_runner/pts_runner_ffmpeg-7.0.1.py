#!/usr/bin/env python3
"""
PTS Runner for ffmpeg-7.0.1

System Dependencies (from phoronix-test-suite info):
- Software Dependencies:
  * C/C++ Compiler Toolchain
  * Yasm Assembler
  * Nasm Assembler
  * Python
  * CMake
- Estimated Install Time: 453 Seconds
- Environment Size: 1300 MB
- Test Type: Processor
- Supported Platforms: Linux

Test Characteristics:
- Multi-threaded: Yes (video encoding is highly parallel)
- Honors CFLAGS/CXXFLAGS: Yes
- Notable Instructions: SVE2 support for ARM architectures
- THFix_in_compile: false - Thread count NOT fixed at compile time
- THChange_at_runtime: true - Runtime thread configuration via -threads $NUM_CPU_CORES option
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
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

    def download_from_xml(self, benchmark_name, threshold_mb=96):
        """
        Parse downloads.xml for the benchmark and download large files.

        Args:
            benchmark_name: Full benchmark name (e.g., "pts/ffmpeg-7.0.1")
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
            print(f"  [WARN] aria2c download failed, falling back to PTS default")
            if target_path.exists():
                target_path.unlink()
            return False


class FFmpegRunner:
    def __init__(self, threads_arg=None, quick_mode=False):
        """
        Initialize FFmpeg runner.

        Args:
            threads_arg: Thread count argument (None for scaling mode, int for fixed mode)
            quick_mode: If True, run tests once (FORCE_TIMES_TO_RUN=1) for development
        """
        self.benchmark = "ffmpeg-7.0.1"
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
            self.thread_list = list(range(2, self.vcpu_count + 1, 2))
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
                print(f"  [INFO] Attempting to adjust perf_event_paranoid to 0...")

                result = subprocess.run(
                    ['sudo', 'sysctl', '-w', 'kernel.perf_event_paranoid=0'],
                    capture_output=True,
                    text=True
                )

                if result.returncode == 0:
                    print(f"  [OK] perf_event_paranoid adjusted to 0 (temporary, until reboot)")
                    return 0
                else:
                    print(f"  [ERROR] Failed to adjust perf_event_paranoid (sudo required)")
                    print(f"  [WARN] Running in LIMITED mode")
                    return current_value
            else:
                print(f"  [OK] perf_event_paranoid={current_value} is acceptable")
                return current_value

        except Exception as e:
            print(f"  [ERROR] Could not check perf_event_paranoid: {e}")
            print(f"  [WARN] Assuming restrictive mode (perf_event_paranoid=2)")
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

        # NOTE: We do NOT remove the test profile here.
        # We want to keep it (or download it) so we can patch downloads.xml.
        # Removing it forces a re-download which might just act weirdly or fail.

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

    def get_ffmpeg_configure_opts(self):
        """
        Build FFmpeg configure extra options for build-time optimization.

        These options reduce build time by:
        - Disabling unnecessary codecs (VP8, VP9, AV1, etc.)
        - Disabling unnecessary protocols (HTTP, RTMP, HLS, etc.)
        - Disabling unnecessary filters
        - Keeping only x264/x265 encoders and minimal decoders

        Returns:
            str: Space-separated configure options
        """
        opts = []

        # Minimal build: disable everything then enable only what's needed
        opts.append('--disable-everything')

        # Enable x264/x265 encoders (required for benchmark)
        opts.append('--enable-encoder=libx264,libx265')

        # Enable decoders needed for PSNR calculation
        opts.append('--enable-decoder=h264,hevc')

        # Enable muxers/demuxers for MP4 and Matroska containers
        opts.append('--enable-muxer=mp4,matroska')
        opts.append('--enable-demuxer=mov,matroska')

        # Enable file protocol (required)
        opts.append('--enable-protocol=file')

        # Enable minimal filters (scale for resizing, null for passthrough)
        opts.append('--enable-filter=null,scale,psnr')

        # Enable parsers for H.264/HEVC
        opts.append('--enable-parser=h264,hevc')

        # Enable bsf (bitstream filters) that might be needed
        opts.append('--enable-bsf=h264_mp4toannexb,hevc_mp4toannexb')

        print(f"  [OPTIMIZATION] Minimal FFmpeg build - only x264/x265 encoders")
        print(f"  [INFO] Skipping 100+ unnecessary codecs and protocols")

        return ' '.join(opts)

    def fix_x264_checksum(self):
        """
        Fix x264 checksum in downloads.xml due to upstream changes.

        VideoLAN's x264 git archive ZIP has changed, causing checksum mismatch.
        This method updates the downloads.xml with the correct checksums.
        """
        downloads_xml = Path.home() / '.phoronix-test-suite' / 'test-profiles' / 'pts' / self.benchmark / 'downloads.xml'

        if not downloads_xml.exists():
            print(f"  [WARN] downloads.xml not found, skipping checksum fix")
            return

        try:
            with open(downloads_xml, 'r') as f:
                content = f.read()

            # Target x264 file: x264-7ed753b10a61d0be95f683289dfb925b800b0676.zip
            # New checksums (actual current file)
            new_md5 = '62a5d6c7207be9f0aa6e1a9552345c8c'
            new_sha256 = 'd859f2b4b0b70d6f10a33e9b5e0a6bf41d97e4414644c4f85f02b7665c0d1292'
            new_size = '1222391'

            # Safer approach: Split content into Package blocks to avoid regex crossing boundaries
            packages = content.split('</Package>')
            output_packages = []
            modified = False

            for pkg in packages:
                # Ensure we only target the x264 package, avoiding false positives (e.g. if x264 is mentioned in ffmpeg block)
                if 'x264-7ed753b10a61d0be95f683289dfb925b800b0676.zip' in pkg and 'ffmpeg-7.0.tar.xz' not in pkg:
                    # found the target package
                    
                    # Update MD5
                    pkg = re.sub(r'<MD5>[a-fA-F0-9]+</MD5>', f'<MD5>{new_md5}</MD5>', pkg)
                    # Update SHA256
                    pkg = re.sub(r'<SHA256>[a-fA-F0-9]+</SHA256>', f'<SHA256>{new_sha256}</SHA256>', pkg)
                    # Update FileSize
                    pkg = re.sub(r'<FileSize>\d+</FileSize>', f'<FileSize>{new_size}</FileSize>', pkg)
                    
                    modified = True
                    print(f"  [DEBUG] Fixed checksums in x264 package block")
                
                output_packages.append(pkg)

            if modified:
                print(f"  [FIX] Updating x264 checksums in downloads.xml (Safe Split)")
                modified_content = '</Package>'.join(output_packages)
                
                with open(downloads_xml, 'w') as f:
                    f.write(modified_content)
                print(f"  [OK] x264 checksums updated")
            else:
                print(f"  [INFO] x264 package not found or already correct (no changes needed)") 
            
        except Exception as e:
            print(f"  [WARN] Failed to fix checksums: {e}")

    def install_benchmark(self):
        """
        Install ffmpeg-7.0.1 with GCC-14 native compilation.

        Note: FFmpeg supports runtime thread configuration via -threads option.
        Since THFix_in_compile=false, NUM_CPU_CORES is NOT set during build.
        """
        # Ensure test profile exists (download if needed) so we can patch it
        print(f"  [INFO] Ensuring test profile exists...")
        subprocess.run(
            ['phoronix-test-suite', 'info', self.benchmark_full],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        # Pre-apply x264 checksum fix to avoid double installation time
        self.fix_x264_checksum()

        # Pre-download large files (vbench is 875MB) with aria2c for speed
        print(f"\n>>> Checking for large files to pre-seed...")
        downloader = PreSeedDownloader()
        downloader.download_from_xml(self.benchmark_full, threshold_mb=96)

        print(f"\n>>> Installing {self.benchmark_full}...")

        # Remove existing installation first
        print(f"  [INFO] Removing existing installation...")
        remove_cmd = f'echo "y" | phoronix-test-suite remove-installed-test "{self.benchmark_full}"'
        print(f"  [INSTALL CMD] {remove_cmd}")
        subprocess.run(
            ['bash', '-c', remove_cmd],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        # Build install command
        nproc = os.cpu_count() or 1

        # Get FFmpeg configure optimization options
        ffmpeg_configure_opts = self.get_ffmpeg_configure_opts()
        print(f"  [INFO] FFmpeg configure extra options: {ffmpeg_configure_opts[:100]}...")

        install_cmd = f'FFMPEG_CONFIGURE_EXTRA_OPTS="{ffmpeg_configure_opts}" MAKEFLAGS="-j{nproc}" CC=gcc-14 CXX=g++-14 CFLAGS="-O3 -march=native -mtune=native" CXXFLAGS="-O3 -march=native -mtune=native" phoronix-test-suite batch-install {self.benchmark_full}'

        # Print install command for debugging
        print(f"\n{'>'*80}")
        print(f"[PTS INSTALL COMMAND]")
        print(f"  {install_cmd[:200]}...")
        print(f"{'<'*80}\n")

        # Execute install command (attempt 1) with real-time output streaming
        print(f"  Running installation (attempt 1)...")
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
        full_output = ''.join(install_output)

        # Check for installation failure
        install_failed = False
        if returncode != 0:
            install_failed = True
        elif pts_test_failed:
            install_failed = True
        elif 'Checksum Failed' in full_output or 'Downloading of needed test files failed' in full_output:
            install_failed = True
        elif 'ERROR' in full_output or 'FAILED' in full_output:
            install_failed = True

        # If first attempt failed due to x264 checksum, fix and retry
        if install_failed and 'x264-7ed753b10a61d0be95f683289dfb925b800b0676.zip' in full_output:
            print(f"\n  [WARN] x264 checksum failure detected, applying fix...")
            self.fix_x264_checksum()

            print(f"  [INFO] Retrying installation after checksum fix...")
            if log_f:
                log_f.write("\n[RETRY AFTER CHECKSUM FIX]\n")
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
            full_output = ''.join(install_output)

            # Re-check for installation failure
            install_failed = False
            if returncode != 0:
                install_failed = True
            elif 'Checksum Failed' in full_output or 'Downloading of needed test files failed' in full_output:
                install_failed = True
            elif 'ERROR' in full_output or 'FAILED' in full_output:
                install_failed = True

        if log_f:
            log_f.close()

        if install_failed:
            # Archive PTS install failure evidence for postmortem
            try:
                failed_logs = sorted((Path.home() / '.phoronix-test-suite' / 'installed-tests' / 'pts' / self.benchmark).rglob('install-failed.log'))
                if failed_logs:
                    debug_dir = self.project_root / 'results' / 'debug_logs'
                    debug_dir.mkdir(parents=True, exist_ok=True)
                    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                    for i, src in enumerate(failed_logs, 1):
                        dst = debug_dir / f'ffmpeg_install-failed.{self.machine_name}.{ts}.{i}.log'
                        shutil.copy2(src, dst)
                        print(f"  [INFO] Archived install-failed.log: {dst}")
            except Exception as archive_error:
                print(f"  [WARN] Failed to archive install-failed.log: {archive_error}")

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

    def parse_perf_stats_and_freq(self, perf_stats_file, freq_start_file, freq_end_file, cpu_list):
        """Parse perf stat output and CPU frequency files."""
        print(f"\n>>> Parsing perf stats and frequency data")

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

        print(f"  [OK] Performance metrics calculated")
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
        print(f"[PTS BENCHMARK COMMAND]")
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

        pts_test_failed = False
        failure_reason = ""
        if log_file.exists():
            try:
                log_content = log_file.read_text(errors='ignore')
                failure_patterns = [
                    ("Multiple tests are not installed", "PTS test profile is not installed"),
                    ("The following tests failed", "PTS reported test execution failure"),
                    ("quit with a non-zero exit status", "PTS benchmark subprocess failed"),
                    ("failed to properly run", "PTS benchmark did not run properly"),
                ]
                for pattern, reason in failure_patterns:
                    if pattern.lower() in log_content.lower():
                        pts_test_failed = True
                        failure_reason = reason
                        break
            except Exception as e:
                print(f"  [WARN] Could not inspect benchmark log for failure patterns: {e}")

        if returncode == 0 and not pts_test_failed:
            print(f"\n[OK] Benchmark completed successfully")

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
            if returncode == 0 and pts_test_failed:
                print(f"\n[ERROR] Benchmark command returned 0 but benchmark did not execute successfully")
                print(f"       Reason: {failure_reason or 'PTS log indicates failure'}")
            else:
                print(f"\n[ERROR] Benchmark failed with return code {returncode}")
            err_file = self.results_dir / f"{num_threads}-thread.err"
            with open(err_file, 'w') as f:
                if returncode == 0 and pts_test_failed:
                    f.write("Benchmark failed: PTS reported failure despite zero exit code\n")
                    if failure_reason:
                        f.write(f"Reason: {failure_reason}\n")
                else:
                    f.write(f"Benchmark failed with return code {returncode}\n")
                f.write(f"See {log_file} for details.\n")
                if returncode == 0 and pts_test_failed:
                    f.write("Hint: ensure test is installed, e.g. `phoronix-test-suite batch-install pts/ffmpeg-7.0.1`\n")
            print(f"     Error log: {err_file}")
            return False

        return True

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

            # Export to JSON
            json_output = self.results_dir / f"{num_threads}-thread.json"
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

        print(f"\n[OK] Export completed")

    def generate_summary(self):
        """Generate summary.log and summary.json from all thread results."""
        print(f"\n{'='*80}")
        print(f">>> Generating summary")
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
            f.write(f"FFmpeg 7.0.1 Benchmark Summary\n")
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
        print(f"FFmpeg 7.0.1 Benchmark Runner")
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
                f"[WARN] Found installation directory but PTS does not recognize {self.benchmark_full}. "
                "Treating as broken install and reinstalling."
            )

        if not already_installed:
            self.clean_pts_cache()
            self.install_benchmark()
        else:
            print(f"[INFO] Benchmark already installed, skipping installation: {self.benchmark_full}")

        failed = []
        for num_threads in self.thread_list:
            if not self.run_benchmark(num_threads):
                failed.append(num_threads)

        self.export_results()
        self.generate_summary()

        print(f"\n{'='*80}")
        print(f"Benchmark Summary")
        print(f"{'='*80}")
        print(f"Total tests: {len(self.thread_list)}")
        print(f"Successful: {len(self.thread_list) - len(failed)}")
        print(f"Failed: {len(failed)}")
        print(f"{'='*80}")

        return len(failed) == 0


def main():
    parser = argparse.ArgumentParser(
        description="FFmpeg 7.0.1 Benchmark Runner",
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

    runner = FFmpegRunner(threads_arg=threads, quick_mode=args.quick)
    success = runner.run()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
