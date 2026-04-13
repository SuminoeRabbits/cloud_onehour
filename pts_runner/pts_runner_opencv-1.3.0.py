#!/usr/bin/env python3
"""
PTS Runner for opencv-1.3.0

System Dependencies (from phoronix-test-suite info):
- Software Dependencies:
  * Libevent
- Test Type: System
- Supported Platforms: Linux, BSD, MacOSX

Test Characteristics:
- Multi-threaded: Yes
- Honors CFLAGS/CXXFLAGS: Yes
- Notable Instructions: N/A
"""
import os
import sys
import subprocess
import argparse
import shutil
import time
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from runner_common import detect_pts_failure_from_log, get_install_status, cleanup_pts_artifacts

class OpenCVRunner:
    def __init__(self, threads_arg=None, quick_mode=False):
        """
        Initialize the OpenCV Runner.

        Args:
            threads_arg: Number of threads (optional). If None, will run in scaling mode.
            quick_mode: If True, run in quick mode (FORCE_TIMES_TO_RUN=1).
        """
        self.benchmark = "opencv-1.3.0"
        self.benchmark_full = "pts/opencv-1.3.0"
        self.test_category = "FPU"
        self.test_category_dir = self.test_category.replace(' ', '_')

        # Test filter configurable variable
        self._KEEP_TESTS = {"dnn", "objdetect", "imgproc", "features2d", "stitching", "video"}

        # Full upstream test catalogue (from pts/opencv-1.3.0 GitHub master).
        # Used to supplement missing entries in the locally-installed
        # test-definition.xml (OpenBenchmarking.org cache may ship a trimmed XML).
        self._ALL_KNOWN_TESTS = {
            "dnn":       "DNN - Deep Neural Network",
            "features2d": "Features 2D",
            "objdetect": "Object Detection",
            "core":      "Core",
            "gapi":      "Graph API",
            "imgproc":   "Image Processing",
            "stitching": "Stitching",
            "video":     "Video",
        }

        # System info
        self.vcpu_count = os.cpu_count() or 1
        self.machine_name = os.environ.get('MACHINE_NAME', os.uname().nodename)
        self.os_name = self.get_os_name()

        # Project structure
        self.script_dir = Path(__file__).parent.resolve()
        self.project_root = self.script_dir.parent

        # Thread configuration
        self.quick_mode = quick_mode

        if threads_arg is None:
            # Even-number scaling: [2, 4, 6, ..., nproc]
            # 4-point scaling: [nproc/4, nproc/2, nproc*3/4, nproc]

            n_4 = self.vcpu_count // 4

            self.thread_list = [n_4, n_4 * 2, n_4 * 3, self.vcpu_count]

            # Remove any zeros and deduplicate

            self.thread_list = sorted(list(set([t for t in self.thread_list if t > 0])))
        else:
            # Fixed mode: cap at vcpu_count
            n = min(threads_arg, self.vcpu_count)
            if n != threads_arg:
                print(f"  [INFO] Thread count {threads_arg} capped to {n} (nproc)")
            self.thread_list = [n]

        # Results directory
        self.results_dir = self.project_root / "results" / self.machine_name / self.os_name / self.test_category_dir / self.benchmark
        
        # Perf configuration
        self.perf_paranoid = self.check_and_setup_perf_permissions()
        # Default events for memory/cpu bound
        self.perf_events = self.get_perf_events()
        # Enforce safety
        self.ensure_upload_disabled()

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
        """
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
        cpu_list = []
        if n <= half:
            cpu_list = [str(i * 2) for i in range(n)]
        else:
            cpu_list = [str(i * 2) for i in range(half)]
            logical_count = n - half
            cpu_list.extend([str(i * 2 + 1) for i in range(logical_count)])
        return ','.join(cpu_list)


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
        """
        perf_path = shutil.which("perf")
        if not perf_path:
            print("  [INFO] perf command not found")
            return None

        # Test HW+SW
        hw_events = "cycles,instructions,branches,branch-misses,cache-references,cache-misses"
        test_cmd = f"perf stat -e {hw_events} -- sleep 0.01"
        result = subprocess.run(['bash', '-c', test_cmd], capture_output=True, text=True)
        if result.returncode == 0:
            if 'not supported' not in (result.stdout + result.stderr):
                return hw_events

        # Test SW only
        sw_events = "cpu-clock,task-clock,context-switches,cpu-migrations,page-faults"
        test_cmd = f"perf stat -e {sw_events} -- sleep 0.01"
        result = subprocess.run(['bash', '-c', test_cmd], capture_output=True, text=True)
        if result.returncode == 0:
            if 'not supported' not in (result.stdout + result.stderr):
                return sw_events
                
        print("  [WARN] perf events not available")
        return None

    def check_and_setup_perf_permissions(self):
        """Check and adjust perf_event_paranoid setting."""
        try:
            result = subprocess.run(
                ['cat', '/proc/sys/kernel/perf_event_paranoid'],
                capture_output=True, text=True, check=True
            )
            current_value = int(result.stdout.strip())
            
            if current_value >= 1:
                print("  [INFO] Attempting to adjust perf_event_paranoid to 0...")
                result = subprocess.run(
                    ['sudo', 'sysctl', '-w', 'kernel.perf_event_paranoid=0'],
                    capture_output=True, text=True
                )
                if result.returncode == 0:
                    return 0
                return current_value
            return current_value
        except Exception:
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

    def patch_test_definition(self) -> bool:
        """Patch test-definition.xml to execute exactly _KEEP_TESTS.

        The XML distributed via OpenBenchmarking.org may only contain a subset
        of the upstream entries (e.g. only 'dnn').  This method:
          1. Adds any _KEEP_TESTS entries that are missing, using _ALL_KNOWN_TESTS
             as the authoritative name->value mapping.
          2. Removes any entries that are NOT in _KEEP_TESTS.
        """
        xml_path = Path.home() / ".phoronix-test-suite" / "test-profiles" / "pts" / self.benchmark / "test-definition.xml"
        bak_path = xml_path.with_suffix(".xml.bak")

        # Pull profile info to ensure XML is present locally
        if not xml_path.exists():
            subprocess.run(['phoronix-test-suite', 'info', self.benchmark_full], capture_output=True)

        if not xml_path.exists():
            print(f"  [WARN] test-definition.xml not found at {xml_path}")
            return False

        try:
            shutil.copy2(xml_path, bak_path)
            tree = ET.parse(xml_path)
            root = tree.getroot()
            test_settings = root.find("TestSettings")
            if test_settings is None:
                return False

            patched = False
            for option in test_settings.findall("Option"):
                ident = option.find("Identifier")
                if ident is None or (ident.text or "").strip() != "test":
                    continue
                menu = option.find("Menu")
                if menu is None:
                    continue

                # --- Step 1: collect existing values ---
                existing_values = set()
                for entry in menu.findall("Entry"):
                    val_node = entry.find("Value")
                    val = (val_node.text or "").strip() if val_node is not None else ""
                    existing_values.add(val)

                # --- Step 2: add missing _KEEP_TESTS entries ---
                for val in sorted(self._KEEP_TESTS):
                    if val not in existing_values:
                        display_name = self._ALL_KNOWN_TESTS.get(val, val)
                        entry = ET.SubElement(menu, "Entry")
                        name_el = ET.SubElement(entry, "Name")
                        name_el.text = display_name
                        val_el = ET.SubElement(entry, "Value")
                        val_el.text = val
                        patched = True
                        print(f"  [PATCH] Added missing test entry: {val} ({display_name})")

                # --- Step 3: remove entries not in _KEEP_TESTS ---
                for entry in menu.findall("Entry"):
                    val_node = entry.find("Value")
                    val = (val_node.text or "").strip() if val_node is not None else ""
                    if val not in self._KEEP_TESTS:
                        menu.remove(entry)
                        patched = True
                        print(f"  [PATCH] Removed test entry: {val}")

            if patched:
                tree.write(xml_path, encoding="utf-8", xml_declaration=True)
                print(f"  [INFO] Patched test-definition.xml to keep only tests: {self._KEEP_TESTS}")
            else:
                print(f"  [INFO] test-definition.xml already matches _KEEP_TESTS: {self._KEEP_TESTS}")
            return True
        except Exception as e:
            print(f"  [ERROR] Failed to patch XML: {e}")
            self.restore_test_definition()
            return False

    def restore_test_definition(self):
        """Restore original test-definition.xml."""
        xml_path = Path.home() / ".phoronix-test-suite" / "test-profiles" / "pts" / self.benchmark / "test-definition.xml"
        bak_path = xml_path.with_suffix(".xml.bak")
        if bak_path.exists():
            shutil.move(str(bak_path), str(xml_path))
            print("  [RESTORE] Restored test-definition.xml")

    # ---------------------------------------------------------------------------
    # OpenCV version patch: override PTS profile to use 4.13.0 instead of 4.7.0
    # ---------------------------------------------------------------------------
    _OPENCV_TARGET_VERSION = "4.13.0"
    _OPENCV_PACKAGES = {
        "opencv": {
            "url_tmpl":   "https://github.com/opencv/opencv/archive/refs/tags/{ver}.tar.gz",
            "filename":   "opencv-{ver}.tar.gz",
            "md5":        "f33c0ace3add57aba7b9d3fe3c41feb4",
            "sha256":     "1d40ca017ea51c533cf9fd5cbde5b5fe7ae248291ddf2af99d4c17cf8e13017d",
            "filesize":   "95420275",
        },
        "opencv_extra": {
            "url_tmpl":   "https://github.com/opencv/opencv_extra/archive/refs/tags/{ver}.tar.gz",
            "filename":   "opencv_extra-{ver}.tar.gz",
            "md5":        "7af2fe54d571c2efa4d67938f81b01b0",
            "sha256":     "a6137c1e5e82010fa212c36f7d48a05b4467e2413bcd9ac6c469f6d398c71f27",
            "filesize":   "17793015",
        },
    }
    # opencv_extra-4.13.0 does NOT ship testdata/dnn/ — those files are stored
    # in Git LFS and are absent from GitHub source archives.  We supplement them
    # from opencv_extra-4.7.0 (last release where dnn testdata was in-repo).
    _OPENCV_EXTRA_TESTDATA = {
        "url":      "https://github.com/opencv/opencv_extra/archive/refs/tags/4.7.0.tar.gz",
        "filename": "opencv_extra-4.7.0.tar.gz",
        "md5":      "051564e9b2b59b01fe93d9ec12525556",
        "sha256":   "835420bbd625ba73ac892bdadf247a52ac42fa26f24c2f3752f63dbb3487bbb5",
        "filesize": "500181420",
    }

    def patch_opencv_version(self) -> bool:
        """Patch PTS opencv profile to use OpenCV 4.13.0.

        Modifies downloads.xml (URLs, checksums, sizes) and install.sh
        (version strings).  Also copies pre-downloaded tarballs from the
        pts_runner directory into the PTS download cache so that the install
        step does not need to re-download them.
        """
        ver = self._OPENCV_TARGET_VERSION
        profile_dir = Path.home() / ".phoronix-test-suite" / "test-profiles" / "pts" / self.benchmark

        # Ensure profile XML is present locally
        downloads_xml = profile_dir / "downloads.xml"
        install_sh    = profile_dir / "install.sh"
        if not downloads_xml.exists():
            subprocess.run(['phoronix-test-suite', 'info', self.benchmark_full], capture_output=True)

        if not downloads_xml.exists():
            print(f"  [WARN] downloads.xml not found at {downloads_xml}; skipping version patch")
            return False

        # --- patch downloads.xml ---
        try:
            bak = downloads_xml.parent / "downloads.xml.bak"
            shutil.copy2(downloads_xml, bak)
            tree = ET.parse(downloads_xml)
            root = tree.getroot()

            downloads_node = root.find("Downloads")
            if downloads_node is None:
                downloads_node = root

            for pkg in root.findall(".//Package"):
                url_node    = pkg.find("URL")
                fname_node  = pkg.find("FileName")
                md5_node    = pkg.find("MD5")
                sha256_node = pkg.find("SHA256")
                size_node   = pkg.find("FileSize")

                if url_node is None or fname_node is None:
                    continue

                fname = (fname_node.text or "").strip()

                if "opencv_extra" in fname:
                    meta = self._OPENCV_PACKAGES["opencv_extra"]
                elif "opencv" in fname:
                    meta = self._OPENCV_PACKAGES["opencv"]
                else:
                    continue

                url_node.text   = meta["url_tmpl"].format(ver=ver)
                fname_node.text = meta["filename"].format(ver=ver)
                if md5_node    is not None: md5_node.text    = meta["md5"]
                if sha256_node is not None: sha256_node.text = meta["sha256"]
                if size_node   is not None: size_node.text   = meta["filesize"]

            # Add supplemental opencv_extra-4.7.0 for full testdata/ (moved to LFS in 4.13.0)
            td = self._OPENCV_EXTRA_TESTDATA
            extra_pkg = ET.SubElement(downloads_node, "Package")
            ET.SubElement(extra_pkg, "URL").text      = td["url"]
            ET.SubElement(extra_pkg, "MD5").text      = td["md5"]
            ET.SubElement(extra_pkg, "SHA256").text   = td["sha256"]
            ET.SubElement(extra_pkg, "FileName").text = td["filename"]
            ET.SubElement(extra_pkg, "FileSize").text = td["filesize"]

            tree.write(downloads_xml, encoding="utf-8", xml_declaration=True)
            print(f"  [PATCH] downloads.xml updated to OpenCV {ver} + full testdata supplement")
        except Exception as e:
            print(f"  [ERROR] Failed to patch downloads.xml: {e}")
            return False

        # --- patch install.sh ---
        if install_sh.exists():
            try:
                bak_sh = install_sh.parent / "install.sh.bak"
                shutil.copy2(install_sh, bak_sh)
                content = install_sh.read_text()

                # Step 1: replace old version strings with 4.13.0
                old_ver_match = re.search(r'4\.\d+\.\d+', content)
                if old_ver_match:
                    old_ver = old_ver_match.group(0)
                    content = content.replace(old_ver, ver)
                    print(f"  [PATCH] install.sh: {old_ver} -> {ver}")

                # Step 2: inject full testdata supplement immediately after
                # `tar -xf opencv_extra-4.13.0.tar.gz`
                #
                # opencv_extra-4.13.0 ships only module source code; all testdata
                # (dnn/, cv/tracking/, cv/shared/, cv/qrcode/, perf/, …) was moved
                # to Git LFS and is absent from the release tarball.
                # opencv_extra-4.7.0 is the last release where testdata was included
                # in-tree.  We extract the entire testdata/ tree from 4.7.0 and merge
                # it into 4.13.0's directory so all perf tests can find their assets:
                #   - dnn/         : DNN model weights / configs
                #   - cv/tracking/ : video test data (faceocc2.webm …)
                #   - cv/shared/   : common images (lena.png …)
                #   - cv/qrcode/   : QR-code test images
                #   - perf/        : XML performance reference data (stitching.xml …)
                extra_extract_marker = f"tar -xf opencv_extra-{ver}.tar.gz"
                testdata_supplement = (
                    f"{extra_extract_marker}\n"
                    f"# Supplement missing testdata/ from opencv_extra-4.7.0\n"
                    f"# (testdata moved to Git LFS in 4.13.0; absent from source archive)\n"
                    f"tar -xf opencv_extra-4.7.0.tar.gz opencv_extra-4.7.0/testdata/\n"
                    f"cp -r opencv_extra-4.7.0/testdata/. opencv_extra-{ver}/testdata/\n"
                    f"rm -rf opencv_extra-4.7.0"
                )
                if extra_extract_marker in content:
                    content = content.replace(extra_extract_marker, testdata_supplement)
                    print(f"  [PATCH] install.sh: full testdata supplement injected")
                else:
                    print(f"  [WARN]  install.sh: marker '{extra_extract_marker}' not found; "
                          f"testdata supplement NOT injected")

                # Step 3: replace cmake command with optimized flags (arch-aware)
                # WITH_KLEIDICV is ARM64-only: ON x86_64 it injects -march=armv8-a
                # which causes a hard build failure with GCC.
                import platform
                machine = platform.machine().lower()
                is_arm64 = machine in ("aarch64", "arm64")
                arch_label = "aarch64" if is_arm64 else "x86_64"

                cmake_old = "cmake -DCMAKE_BUILD_TYPE=Release -DWITH_OPENCL=OFF .."
                cmake_new = (
                    "cmake -DCMAKE_BUILD_TYPE=Release -DWITH_OPENCL=OFF"
                    " -DCPU_BASELINE=DETECT"
                    " -DCPU_DISPATCH=ALL"
                    " -DOPENCV_GENERATE_SETUPVARS=ON"
                    + (" -DWITH_KLEIDICV=ON" if is_arm64 else "")
                    + " -DWITH_IPP=OFF"
                    " .."
                )
                if cmake_old in content:
                    content = content.replace(cmake_old, cmake_new)
                    print(f"  [PATCH] install.sh: cmake SIMD flags injected [{arch_label}]"
                          + (" +KLEIDICV" if is_arm64 else " KLEIDICV=OFF(x86)"))
                else:
                    print(f"  [WARN]  install.sh: cmake marker not found; SIMD flags NOT injected")

                install_sh.write_text(content)
            except Exception as e:
                print(f"  [ERROR] Failed to patch install.sh: {e}")
                return False

        # --- seed PTS download cache with local tarballs ---
        cache_dir = Path.home() / ".phoronix-test-suite" / "download-cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        seed_files = [
            self._OPENCV_PACKAGES["opencv"]["filename"].format(ver=ver),
            self._OPENCV_PACKAGES["opencv_extra"]["filename"].format(ver=ver),
            self._OPENCV_EXTRA_TESTDATA["filename"],
        ]
        for fname in seed_files:
            src = self.script_dir / fname
            dst = cache_dir / fname
            if src.exists():
                if not dst.exists():
                    shutil.copy2(str(src), str(dst))
                    print(f"  [CACHE] Seeded {fname} into PTS download cache")
                else:
                    print(f"  [CACHE] {fname} already in PTS download cache")
            else:
                print(f"  [WARN]  Local tarball not found: {src} (will attempt download)")

        return True

    def restore_opencv_version(self):
        """Restore original downloads.xml and install.sh from backups."""
        profile_dir = Path.home() / ".phoronix-test-suite" / "test-profiles" / "pts" / self.benchmark
        for orig_name, bak_name in [("downloads.xml", "downloads.xml.bak"), ("install.sh", "install.sh.bak")]:
            bak  = profile_dir / bak_name
            orig = profile_dir / orig_name
            if bak.exists():
                shutil.move(str(bak), str(orig))
                print(f"  [RESTORE] Restored {orig_name}")

    def _is_target_version_installed(self) -> bool:
        """Return True when the OpenCV 4.13.0 build artefacts are present."""
        ver = self._OPENCV_TARGET_VERSION
        installed_base = Path.home() / ".phoronix-test-suite" / "installed-tests" / "pts" / self.benchmark
        # install.sh unpacks to opencv-{ver}/ and builds under opencv-{ver}/build/
        opencv_build = installed_base / f"opencv-{ver}" / "build"
        return opencv_build.is_dir()

    def clean_pts_cache(self):
        """Clean PTS installed tests."""
        print(">>> Cleaning PTS cache...")
        pts_home = Path.home() / '.phoronix-test-suite'
        installed_dir = pts_home / 'installed-tests' / 'pts' / self.benchmark.split('-')[0]
        if installed_dir.exists():
             shutil.rmtree(installed_dir)
        print("  [OK] PTS cache cleaned")

    def install_benchmark(self):
        """Install benchmark."""
        print(f"\n>>> Installing {self.benchmark_full}...")
        
        # Remove existing
        subprocess.run(['bash', '-c', f'echo "y" | phoronix-test-suite remove-installed-test "{self.benchmark_full}"'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # Install
        nproc = os.cpu_count() or 1
        install_cmd = f'NUM_CPU_CORES={nproc} phoronix-test-suite batch-install {self.benchmark_full}'
        
        install_log_env = os.environ.get("PTS_INSTALL_LOG", "").strip().lower()
        install_log_path = os.environ.get("PTS_INSTALL_LOG_PATH", "").strip()
        use_install_log = install_log_env in {"1", "true", "yes"} or bool(install_log_path)
        install_log = Path(install_log_path) if install_log_path else (self.results_dir / "install.log")
        log_file = install_log
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
        
        output = []
        for line in process.stdout:
            print(line, end='')
            if log_f:
                log_f.write(line)
                log_f.flush()
            output.append(line)
        process.wait()
        if log_f:
            log_f.close()
        
        # Always write install log so detect_pts_failure_from_log can read it
        try:
            self.results_dir.mkdir(parents=True, exist_ok=True)
            with open(install_log, 'w') as f:
                f.writelines(output)
        except Exception as e:
            print(f"  [WARN] Could not write install log: {e}")

        pts_test_failed, pts_failure_reason = detect_pts_failure_from_log(log_file)
        install_failed = False
        full_output = ''.join(output)
        if process.returncode != 0:
            install_failed = True
        elif pts_test_failed:
            install_failed = True
        elif 'exited with a non-zero exit status' in full_output.lower():
            install_failed = True
        elif 'FAILED' in full_output or 'ERROR:' in full_output:
            install_failed = True

        if install_failed:
            print("  [ERROR] Installation failed")
            if pts_failure_reason:
                print(f"  [ERROR] Reason: {pts_failure_reason}")
            print(f"  [INFO] Install log: {install_log}")
            sys.exit(1)
            
        # Verify
        verify_cmd = f'phoronix-test-suite test-installed {self.benchmark_full}'
        if subprocess.run(['bash', '-c', verify_cmd], capture_output=True).returncode == 0:
             print("  [OK] Installation verified")
        else:
             print("  [WARN] Installation verification skipped/failed")

    def parse_perf_stats_and_freq(self, perf_file, freq_start, freq_end, cpu_list):
        """Parse perf and frequency data."""
        try:
            if perf_file and Path(perf_file).exists():
                pass  # Stub: full perf parsing not implemented
        except FileNotFoundError:
            pass
        return {}

    def run_benchmark(self, num_threads):
        """Run benchmark with specified threads."""
        print(f"\n>>> Running {self.benchmark} with {num_threads} threads")
        
        self.results_dir.mkdir(parents=True, exist_ok=True)
        log_file = self.results_dir / f"{num_threads}-thread.log"
        stdout_log = self.results_dir / "stdout.log"
        perf_stats_file = self.results_dir / f"{num_threads}-thread_perf_stats.txt"
        freq_start_file = self.results_dir / f"{num_threads}-thread_freq_start.txt"
        freq_end_file = self.results_dir / f"{num_threads}-thread_freq_end.txt"

        quick_env = 'FORCE_TIMES_TO_RUN=1 ' if self.quick_mode else ''
        quick_thread_timeout = int(os.environ.get('OPENCV_QUICK_THREAD_TIMEOUT', '1800'))
        normal_thread_timeout = int(os.environ.get('OPENCV_THREAD_TIMEOUT', '5400'))
        thread_timeout = quick_thread_timeout if self.quick_mode else normal_thread_timeout

        def cleanup_stale_memcached_processes():
            cleanup_cmds = [
                "pkill -f 'opencv_benchmark.*memcache_text' || true",
                "pkill -f '^opencv_benchmark' || true",
                "pkill -f '^./memcached -c 4096 -t ' || true",
                "pkill -f '/opt/phoronix-test-suite/phoronix-test-suite batch-run pts/opencv-1.3.0' || true",
            ]
            for c in cleanup_cmds:
                subprocess.run(['bash', '-c', c], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        cleanup_stale_memcached_processes()
        time.sleep(1)

        batch_env = f'{quick_env}NUM_CPU_CORES={num_threads} BATCH_MODE=1 SKIP_ALL_PROMPTS=1 DISPLAY_COMPACT_RESULTS=1 TEST_RESULTS_NAME={self.benchmark}-{num_threads}threads TEST_RESULTS_IDENTIFIER={self.benchmark}-{num_threads}threads TEST_RESULTS_DESCRIPTION={self.benchmark}-{num_threads}threads'
        
        pts_base_cmd = f'phoronix-test-suite batch-run {self.benchmark_full}'

        if self.perf_events:
            inner_cmd = f'perf stat -e {self.perf_events} -o {perf_stats_file} {pts_base_cmd}'
        else:
            inner_cmd = pts_base_cmd

        timeout_cmd = shutil.which('timeout')
        if timeout_cmd:
            # env vars must precede the timeout binary, not be passed as its argument
            pts_cmd = f'{batch_env} {timeout_cmd} --signal=TERM --kill-after=30s {thread_timeout}s {inner_cmd}'
        else:
            pts_cmd = f'{batch_env} {inner_cmd}'

        # Record start freq (cross-platform: x86_64, ARM64, cloud VMs)
        self.record_cpu_frequency(freq_start_file)

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
            process.wait()
            returncode = process.returncode
            stdout_f.write(f"\n[PTS EXIT CODE] {returncode}\n")
            stdout_f.flush()

        pts_test_failed, pts_failure_reason = detect_pts_failure_from_log(log_file)

        cleanup_stale_memcached_processes()

        # Remove PTS result after run to clean up for next invocation
        sanitized_benchmark = self.benchmark.replace('.', '')
        remove_cmds = [
            f'phoronix-test-suite remove-result {self.benchmark}-{num_threads}threads',
            f'phoronix-test-suite remove-result {sanitized_benchmark}-{num_threads}threads'
        ]
        for cmd in remove_cmds:
            subprocess.run(['bash', '-c', cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Record end freq (cross-platform: x86_64, ARM64, cloud VMs)
        self.record_cpu_frequency(freq_end_file)
        
        if returncode == 124:
            print(f"  [ERROR] OpenCV benchmark timed out at {thread_timeout}s for {num_threads} threads")
            return False
        if returncode == 0 and pts_test_failed:
            print(f"\n[ERROR] PTS reported benchmark failure despite zero exit code: {pts_failure_reason}")
            return False

        if returncode == 0:
            return True
        return False

    def export_results(self):
        """Export results to CSV/JSON."""
        for num_threads in self.thread_list:
            result_name = f"{self.benchmark}-{num_threads}threads"
            result_dir_name = result_name.replace('.', '')
            
            # CSV
            subprocess.run(['phoronix-test-suite', 'result-file-to-csv', result_dir_name], capture_output=True)
            home_csv = Path.home() / f"{result_dir_name}.csv"
            if home_csv.exists():
                shutil.move(str(home_csv), str(self.results_dir / f"{num_threads}-thread.csv"))
                
            # JSON
            subprocess.run(['phoronix-test-suite', 'result-file-to-json', result_dir_name], capture_output=True)
            home_json = Path.home() / f"{result_dir_name}.json"
            if home_json.exists():
                shutil.move(str(home_json), str(self.results_dir / f"{num_threads}-thread.json"))

    def generate_summary(self):
        """Generate summary logs."""
        summary_log = self.results_dir / "summary.log"
        with open(summary_log, 'w') as f:
            f.write(f"Summary for {self.benchmark}\n")

    def run(self):
        """Main execution flow."""
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

        try:
            self.patch_test_definition()
            self.patch_opencv_version()

            # Force reinstall when the installed binary does not match the
            # target version (e.g. previously built with 4.7.0).
            if already_installed and not self._is_target_version_installed():
                print(
                    f"[INFO] Installed OpenCV does not match target "
                    f"{self._OPENCV_TARGET_VERSION}; forcing reinstall."
                )
                already_installed = False

            if not already_installed:
                self.clean_pts_cache()
                self.install_benchmark()
            else:
                print(f"[INFO] Benchmark already installed, skipping installation: {self.benchmark_full}")

            for t in self.thread_list:
                self.run_benchmark(t)

            self.export_results()
            self.generate_summary()
            cleanup_pts_artifacts(self.benchmark)
            return True
        finally:
            self.restore_test_definition()
            self.restore_opencv_version()

def main():
    parser = argparse.ArgumentParser(description="OpenCV Runner")
    parser.add_argument('threads_pos', nargs='?', type=int, help='Threads (positional)')
    parser.add_argument('--threads', type=int, help='Threads (named)')
    parser.add_argument('--quick', action='store_true', help='Quick mode')
    args = parser.parse_args()

    threads = args.threads if args.threads else args.threads_pos
    runner = OpenCVRunner(threads_arg=threads, quick_mode=args.quick)
    success = runner.run()
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
