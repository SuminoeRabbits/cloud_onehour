#!/usr/bin/env python3
"""
PTS Runner for spark-1.0.1

System Dependencies (from phoronix-test-suite info):
- Software Dependencies:
  * Java
  * Python
- Estimated Install Time: 7 Seconds
- Environment Size: 2100 MB
- Test Type: System
- Supported Platforms: Linux

Test Characteristics:
- Multi-threaded: Yes (Spark manages parallelism internally via spark-defaults.conf)
- THFix_in_compile: false
- THChange_at_runtime: true
- TH_scaling: spark-internal (auto-detect, configurable via spark-defaults.conf)

Platform Notes:
- JDK 17 required: setup_jdkxx.sh installs via dnf (EL9/EL10) or Adoptium Temurin.
  Spark 3.3.0 + JDK17 requires --add-opens flags; set via JDK_JAVA_OPTIONS at runtime.
- Python 3.12+ compatibility: Spark 3.3.0 is not fully compatible with Python 3.12+.
  This runner sets PYSPARK_PYTHON=python3.11 (or python3.10) when Python >= 3.12 is
  detected and an older Python is available. On RHEL 9 (default Python 3.11) and
  Ubuntu 22.04 (Python 3.10) this fallback is not needed.
- Thread scaling: spark.default.parallelism and spark.executor.cores are updated in
  spark-defaults.conf before each thread-count run (4-point scaling).
- No C compilation: install_cmd has no CC/CFLAGS — Java/Python only.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import zipfile
from pathlib import Path
from runner_common import detect_pts_failure_from_log, get_install_status, cleanup_pts_artifacts

# JDK17 + Spark 3.3.0 compatibility: --add-opens required for reflection access
_SPARK_JAVA_OPTS = (
    "--add-opens=java.base/java.lang=ALL-UNNAMED "
    "--add-opens=java.base/java.lang.invoke=ALL-UNNAMED "
    "--add-opens=java.base/java.lang.reflect=ALL-UNNAMED "
    "--add-opens=java.base/java.io=ALL-UNNAMED "
    "--add-opens=java.base/java.net=ALL-UNNAMED "
    "--add-opens=java.base/java.nio=ALL-UNNAMED "
    "--add-opens=java.base/java.util=ALL-UNNAMED "
    "--add-opens=java.base/java.util.concurrent=ALL-UNNAMED "
    "--add-opens=java.base/java.util.concurrent.atomic=ALL-UNNAMED "
    "--add-opens=java.base/sun.nio.ch=ALL-UNNAMED "
    "--add-opens=java.base/sun.nio.cs=ALL-UNNAMED "
    "--add-opens=java.base/sun.security.action=ALL-UNNAMED "
    "--add-opens=java.base/sun.util.calendar=ALL-UNNAMED "
    "--add-opens=java.security.jgss/sun.security.krb5=ALL-UNNAMED "
    "--add-opens=java.base/javax.security.auth=ALL-UNNAMED"
)


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


class SparkRunner:
    def __init__(self, threads_arg=None, quick_mode=False):
        """
        Initialize Apache Spark benchmark runner.

        TH_scaling=spark-internal: Spark auto-detects cores.
        spark.default.parallelism and spark.executor.cores are updated in
        spark-defaults.conf before each run to implement 4-point thread scaling.
        """
        self.benchmark = "spark-1.0.1"
        self.benchmark_full = f"pts/{self.benchmark}"
        self.test_category = "Network"
        self.test_category_dir = self.test_category.replace(" ", "_")

        self.vcpu_count = os.cpu_count() or 1
        self.machine_name = os.environ.get('MACHINE_NAME', os.uname().nodename)
        self.os_name = self.get_os_name()

        # Python 3.12+ compatibility: Spark 3.3.0 needs Python < 3.12
        self.spark_python_exec = None
        if sys.version_info >= (3, 12):
            for candidate in ("python3.11", "python3.10"):
                if shutil.which(candidate):
                    self.spark_python_exec = candidate
                    break
            if self.spark_python_exec:
                print(f"  [INFO] Python 3.12+ detected; Spark will use {self.spark_python_exec}")
            else:
                print("  [WARN] Python >= 3.12 but no python3.11/3.10 found; Spark 3.3 may fail")

        # 4-point thread scaling: [nproc/4, nproc/2, nproc*3/4, nproc]
        if threads_arg is None:
            n_4 = self.vcpu_count // 4
            self.thread_list = [n_4, n_4 * 2, n_4 * 3, self.vcpu_count]
            self.thread_list = sorted(list(set([t for t in self.thread_list if t > 0])))
        else:
            self.thread_list = [min(threads_arg, self.vcpu_count)]

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
    # Spark-specific helpers
    # ------------------------------------------------------------------

    def find_spark_home(self):
        """
        Find spark-3.3.0-bin-hadoop3 directory.

        install.sh writes spark-defaults.conf to ~/spark-3.3.0-bin-hadoop3/conf/
        using '~/' prefix (HOME-relative). Check $HOME first, then installed-tests.
        """
        home_spark = Path.home() / 'spark-3.3.0-bin-hadoop3'
        if home_spark.exists():
            return home_spark
        pts_spark = (Path.home() / '.phoronix-test-suite' / 'installed-tests'
                     / 'pts' / self.benchmark / 'spark-3.3.0-bin-hadoop3')
        if pts_spark.exists():
            return pts_spark
        return None

    def setup_spark_parallelism(self, num_threads):
        """
        Update spark-defaults.conf before each run to control thread count.

        Sets:
          spark.driver.memory          6g   (from original install.sh)
          spark.default.parallelism    N    (thread scaling)
          spark.executor.cores         N    (executor thread count)
          spark.pyspark.python         X    (when Python 3.12+ detected, force python3.11)

        spark.pyspark.python is the conf-file counterpart of PYSPARK_PYTHON env var.
        Setting it here provides belt-and-suspenders coverage for cases where PTS
        does not propagate env vars to the spark-submit child process.
        """
        spark_home = self.find_spark_home()
        if spark_home is None:
            print("  [WARN] spark-3.3.0-bin-hadoop3 not found, skipping parallelism setup")
            return
        conf_file = spark_home / 'conf' / 'spark-defaults.conf'
        lines = [
            "spark.driver.memory              6g",
            f"spark.default.parallelism        {num_threads}",
            f"spark.executor.cores             {num_threads}",
        ]
        if self.spark_python_exec:
            # Spark 3.x reads this property and sets PYSPARK_PYTHON before spawning workers.
            # Fixes: _pickle.PicklingError with CloudPickle 2.2.0 on Python 3.12+
            lines.append(f"spark.pyspark.python             {self.spark_python_exec}")
        conf_file.write_text('\n'.join(lines) + '\n')
        msg = f"parallelism={num_threads}, executor.cores={num_threads}"
        if self.spark_python_exec:
            msg += f", pyspark.python={self.spark_python_exec}"
        print(f"  [OK] spark-defaults.conf: {msg}")

    # ------------------------------------------------------------------
    # Spark wrapper patching
    # ------------------------------------------------------------------

    def patch_spark_wrapper(self):
        """
        Patch the PTS-generated 'spark' wrapper script after installation.

        Injects explicit PYSPARK_PYTHON / PYSPARK_DRIVER_PYTHON exports so that
        the correct Python is used regardless of whether PTS propagates env vars
        to its test subprocesses.

        This fixes:
          _pickle.PicklingError: Could not serialize object: IndexError: tuple index out of range
        which occurs when CloudPickle 2.2.0 (bundled with PySpark 3.3.0) runs under
        Python 3.12 instead of Python 3.11.

        Idempotent: safe to call multiple times (marker comment used to detect existing patch).
        """
        if not self.spark_python_exec:
            return  # No Python override needed

        installed_dir = (Path.home() / '.phoronix-test-suite' / 'installed-tests'
                         / 'pts' / self.benchmark)
        spark_wrapper = installed_dir / 'spark'
        if not spark_wrapper.exists():
            print(f"  [WARN] spark wrapper not found: {spark_wrapper}")
            return

        content = spark_wrapper.read_text()
        marker = '# PATCHED: PYSPARK_PYTHON override'
        if marker in content:
            print("  [INFO] spark wrapper already patched for Python override")
            return

        # Inject exports right after the shebang line
        inject = (
            f"\n{marker}\n"
            f"export PYSPARK_PYTHON={self.spark_python_exec}\n"
            f"export PYSPARK_DRIVER_PYTHON={self.spark_python_exec}\n"
        )
        lines = content.splitlines(keepends=True)
        if lines and lines[0].startswith('#!'):
            patched = lines[0] + inject + ''.join(lines[1:])
        else:
            patched = inject + content

        spark_wrapper.write_text(patched)
        spark_wrapper.chmod(0o755)
        print(f"  [OK] spark wrapper patched: PYSPARK_PYTHON={self.spark_python_exec}")

    # ------------------------------------------------------------------
    # CloudPickle upgrade
    # ------------------------------------------------------------------

    def upgrade_pyspark_cloudpickle(self):
        """
        Replace PySpark 3.3.0's bundled cloudpickle 2.2.0 with cloudpickle 2.2.1+.

        Root cause of PicklingError on Python 3.11/3.12:
          Python 3.11 added co_qualname to code objects; CloudPickle 2.2.0 does not
          handle this → _pickle.PicklingError: Could not serialize object:
          IndexError: tuple index out of range

        Mechanism (no system Python modification):
          Spark PYTHONPATH: ${SPARK_HOME}/python:${SPARK_HOME}/python/lib/pyspark.zip
          Python searches directories before zip files, so extracting pyspark.zip to
          ${SPARK_HOME}/python/ creates a real pyspark/ directory that takes precedence
          over the bundled zip. We then replace pyspark/cloudpickle/ with 2.2.1+.

        Idempotent: marker file .cloudpickle_upgraded prevents repeat work.
        """
        spark_home = self.find_spark_home()
        if spark_home is None:
            print("  [WARN] upgrade_pyspark_cloudpickle: spark home not found, skipping")
            return

        pyspark_zip = spark_home / 'python' / 'lib' / 'pyspark.zip'
        if not pyspark_zip.exists():
            print(f"  [WARN] upgrade_pyspark_cloudpickle: pyspark.zip not found: {pyspark_zip}")
            return

        spark_python_dir = spark_home / 'python'
        marker = spark_python_dir / '.cloudpickle_upgraded'
        if marker.exists():
            print("  [INFO] upgrade_pyspark_cloudpickle: already upgraded (marker found)")
            return

        print(f"\n>>> Upgrading bundled PySpark cloudpickle (Python {sys.version_info.major}.{sys.version_info.minor} compat)")

        # Step 1: Extract pyspark.zip → spark_home/python/pyspark/
        # This makes pyspark/ directory take precedence over pyspark.zip in PYTHONPATH.
        pyspark_extracted = spark_python_dir / 'pyspark'
        if not pyspark_extracted.exists():
            print(f"  [INFO] Extracting {pyspark_zip} → {spark_python_dir}/")
            try:
                with zipfile.ZipFile(pyspark_zip, 'r') as zf:
                    zf.extractall(str(spark_python_dir))
                print("  [OK] Extracted pyspark.zip")
            except Exception as e:
                print(f"  [ERROR] Failed to extract pyspark.zip: {e}")
                return
        else:
            print(f"  [INFO] {pyspark_extracted} already exists, skipping extract")

        # Step 2: pip install cloudpickle>=2.2.1 into a temp directory
        with tempfile.TemporaryDirectory() as tmpdir:
            print(f"  [INFO] pip install cloudpickle>=2.2.1 --target {tmpdir}")
            pip_result = subprocess.run(
                [sys.executable, '-m', 'pip', 'install',
                 'cloudpickle>=2.2.1', '--target', tmpdir,
                 '--no-deps', '--quiet'],
                capture_output=True, text=True
            )
            if pip_result.returncode != 0:
                print("  [ERROR] pip install cloudpickle failed:")
                print(pip_result.stderr.strip())
                return

            # Step 3: Find the installed cloudpickle package dir
            installed_cp = Path(tmpdir) / 'cloudpickle'
            if not installed_cp.exists():
                print(f"  [ERROR] cloudpickle dir not found in pip target: {tmpdir}")
                return

            # Verify version
            version_file = Path(tmpdir) / 'cloudpickle' / '__init__.py'
            cp_version = 'unknown'
            if version_file.exists():
                for vline in version_file.read_text().splitlines():
                    if '__version__' in vline:
                        cp_version = vline.strip()
                        break

            # Step 4: Replace pyspark/cloudpickle/ with the upgraded version
            dest_cp = pyspark_extracted / 'cloudpickle'
            if dest_cp.exists():
                # Clear __pycache__ to prevent Python from using stale bytecode
                # (Python validates .pyc against source mtime, but being explicit is safer)
                cache_dir = dest_cp / '__pycache__'
                if cache_dir.exists():
                    shutil.rmtree(str(cache_dir))
                    print("  [OK] Cleared pyspark/cloudpickle/__pycache__")
                shutil.rmtree(str(dest_cp))
            shutil.copytree(str(installed_cp), str(dest_cp))
            print(f"  [OK] Replaced pyspark/cloudpickle/ with upgraded version ({cp_version})")
            # Show what files were installed
            installed_files = sorted(f.name for f in dest_cp.iterdir())
            print(f"  [OK] cloudpickle files: {installed_files}")

        # Step 5: Write marker for idempotency
        marker.write_text(f"cloudpickle upgraded for Python {sys.version_info.major}.{sys.version_info.minor}\n")
        print("  [OK] upgrade_pyspark_cloudpickle complete")

    # ------------------------------------------------------------------
    # Spark environment diagnostic
    # ------------------------------------------------------------------

    def diagnose_spark_setup(self):
        """
        Run spark-submit directly with a minimal diagnostic script to verify
        the actual Python/cloudpickle environment that Spark uses.

        This bypasses PTS and the spark wrapper to directly test:
          - Which Python executable the driver actually runs
          - Whether pyspark imports our upgraded cloudpickle (not the zip version)
          - Whether cloudpickle can serialize a lambda (the actual operation that fails)

        Saves full output to _spark_diagnostic.log in results dir.
        """
        spark_home = self.find_spark_home()
        if spark_home is None:
            print("  [DIAG] Skipping: spark home not found")
            return

        spark_submit = spark_home / 'bin' / 'spark-submit'
        if not spark_submit.exists():
            print(f"  [DIAG] Skipping: spark-submit not found at {spark_submit}")
            return

        print(f"\n{'='*80}")
        print(">>> Spark environment diagnostic (pre-benchmark)")
        print(f"{'='*80}")
        print(f"  SPARK_HOME: {spark_home}")

        # Write minimal diagnostic script
        diag_script = self.results_dir / '_spark_diag.py'
        diag_script.write_text(textwrap.dedent("""\
            import sys
            print("[SPARK-DIAG] Python executable:", sys.executable)
            print("[SPARK-DIAG] Python version:", sys.version.replace("\\n", " "))
            print("[SPARK-DIAG] sys.path entries:")
            for p in sys.path:
                print("[SPARK-DIAG]   ", p)
            try:
                import pyspark.cloudpickle as cp
                print("[SPARK-DIAG] pyspark.cloudpickle.__file__:", cp.__file__)
                # Try reading version from cloudpickle.py
                import os
                cp_dir = os.path.dirname(cp.__file__)
                cp_main = os.path.join(cp_dir, "cloudpickle.py")
                version_found = "(version unknown)"
                try:
                    with open(cp_main) as f:
                        for line in f:
                            if "__version__" in line and "=" in line and not line.strip().startswith("#"):
                                version_found = line.strip()
                                break
                except Exception as ve:
                    version_found = f"(read error: {ve})"
                print("[SPARK-DIAG] cloudpickle version:", version_found)
                # List files in cloudpickle dir
                files = sorted(os.listdir(cp_dir))
                print("[SPARK-DIAG] cloudpickle dir files:", files)
                # Test pickling a lambda (the actual operation that fails)
                fn = lambda x: x * 2
                data = cp.dumps(fn)
                print("[SPARK-DIAG] cp.dumps(lambda): OK ({} bytes)".format(len(data)))
                import pickle
                result = pickle.loads(data)
                print("[SPARK-DIAG] pickle.loads + call: OK (result={})".format(result(21)))
            except ImportError as e:
                print("[SPARK-DIAG] ImportError:", e)
            except Exception as e:
                import traceback
                print("[SPARK-DIAG] ERROR:", type(e).__name__, str(e))
                traceback.print_exc()
        """))

        diag_log = self.results_dir / '_spark_diagnostic.log'

        # Run with same env overrides as benchmark, but directly (no PTS/wrapper)
        env = os.environ.copy()
        env['SPARK_HOME'] = str(spark_home)
        if self.spark_python_exec:
            env['PYSPARK_PYTHON'] = self.spark_python_exec
            env['PYSPARK_DRIVER_PYTHON'] = self.spark_python_exec
        env['JDK_JAVA_OPTIONS'] = _SPARK_JAVA_OPTS
        env['SPARK_JAVA_OPTS'] = _SPARK_JAVA_OPTS
        # Clear PYTHONPATH so spark-submit constructs it from scratch.
        # If inherited PYTHONPATH shadows spark_home/python, the diagnostic
        # would not reflect what PTS sees (PTS runs with a clean shell env).
        env.pop('PYTHONPATH', None)

        try:
            result = subprocess.run(
                [str(spark_submit), '--master', 'local[1]',
                 '--driver-memory', '512m', str(diag_script)],
                capture_output=True, text=True, env=env, timeout=120
            )
            full_output = result.stdout + result.stderr
            diag_log.write_text(full_output)
            print(f"  [DIAG] Full diagnostic log: {diag_log}")
            # Print SPARK-DIAG lines inline
            diag_lines = [l for l in full_output.splitlines() if '[SPARK-DIAG]' in l]
            if diag_lines:
                for line in diag_lines:
                    print(f"  {line}")
            else:
                print("  [DIAG] No [SPARK-DIAG] lines found — spark-submit may have failed")
                print("  [DIAG] Last 15 lines of diagnostic output:")
                for line in full_output.splitlines()[-15:]:
                    print(f"    {line}")
            if result.returncode != 0:
                print(f"  [DIAG] spark-submit exit code: {result.returncode}")
        except subprocess.TimeoutExpired:
            print("  [WARN] Spark diagnostic timed out (>120s)")
        except Exception as e:
            print(f"  [WARN] Spark diagnostic failed: {e}")

    # ------------------------------------------------------------------
    # Installation
    # ------------------------------------------------------------------

    def install_benchmark(self):
        """
        Install spark-1.0.1.

        No C compilation: Spark is Java/Python only. No CC/CFLAGS needed.
        JDK 17 must be installed (setup_jdkxx.sh handles this).
        """
        print(f"\n>>> Installing {self.benchmark_full}...")

        # Pre-download large archives with aria2c
        print("\n>>> Checking for large files to pre-seed...")
        downloader = PreSeedDownloader()
        downloader.download_from_xml(self.benchmark_full, threshold_mb=96)

        # Remove existing installation
        print("  [INFO] Removing existing installation...")
        remove_cmd = f'echo "y" | phoronix-test-suite remove-installed-test "{self.benchmark_full}"'
        subprocess.run(
            ['bash', '-c', remove_cmd],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

        # Install command — no CC/CFLAGS (Java/Python benchmark)
        install_cmd = f'phoronix-test-suite batch-install {self.benchmark_full}'

        print(f"\n{'>'*80}")
        print("[PTS INSTALL COMMAND]")
        print(f"  {install_cmd}")
        print(f"{'<'*80}\n")

        install_log_env = os.environ.get("PTS_INSTALL_LOG", "").strip().lower()
        install_log_path = os.environ.get("PTS_INSTALL_LOG_PATH", "").strip()
        use_install_log = install_log_env in {"1", "true", "yes"} or bool(install_log_path)
        install_log = (
            Path(install_log_path) if install_log_path
            else (self.results_dir / "install.log")
        )
        self.results_dir.mkdir(parents=True, exist_ok=True)
        log_f = open(install_log, 'w') if use_install_log else None
        if log_f:
            log_f.write(f"[PTS INSTALL COMMAND]\n{install_cmd}\n\n")
            log_f.flush()

        print("  [INFO] Starting installation (downloading ~2 GB, may take several minutes)...")
        process = subprocess.Popen(
            ['bash', '-c', install_cmd],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1
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
            if use_install_log:
                print(f"  [INFO] Install log: {install_log}")
            sys.exit(1)

        # Verify PTS installed-tests directory
        pts_home = Path.home() / '.phoronix-test-suite'
        installed_dir = pts_home / 'installed-tests' / 'pts' / self.benchmark
        if not installed_dir.exists():
            print(f"  [ERROR] Installation verification failed: {installed_dir} not found")
            sys.exit(1)

        # Verify spark-3.3.0-bin-hadoop3 exists (needed for spark-defaults.conf)
        spark_home = self.find_spark_home()
        if spark_home is None:
            print("  [WARN] spark-3.3.0-bin-hadoop3 not found after install")
            print("  [INFO] spark-defaults.conf updates will be skipped at runtime")
        else:
            print(f"  [OK] Spark home: {spark_home}")

        # Secondary: PTS recognition check (warning only)
        verify_result = subprocess.run(
            ['bash', '-c', f'phoronix-test-suite test-installed {self.benchmark_full}'],
            capture_output=True, text=True
        )
        if verify_result.returncode != 0:
            print("  [WARN] PTS test-installed check failed, but directory exists — continuing")

        print(f"  [OK] Installation verified: {installed_dir}")

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
        Run spark-1.0.1 benchmark with the given thread count.

        Thread scaling: update spark-defaults.conf (spark.default.parallelism,
        spark.executor.cores) before each run. Use taskset for sub-full runs.
        """
        print(f"\n{'='*80}")
        print(f">>> Running benchmark with {num_threads} thread(s)")
        print(f"{'='*80}")

        # Update spark-defaults.conf for this thread count
        self.setup_spark_parallelism(num_threads)

        self.results_dir.mkdir(parents=True, exist_ok=True)
        log_file = self.results_dir / f"{num_threads}-thread.log"
        stdout_log = self.results_dir / "stdout.log"
        perf_stats_file = self.results_dir / f"{num_threads}-thread_perf_stats.txt"
        freq_start_file = self.results_dir / f"{num_threads}-thread_freq_start.txt"
        freq_end_file = self.results_dir / f"{num_threads}-thread_freq_end.txt"
        perf_summary_file = self.results_dir / f"{num_threads}-thread_perf_summary.json"

        # CPU affinity: all CPUs for full run, taskset for sub-full
        if num_threads >= self.vcpu_count:
            cpu_list = ','.join(str(i) for i in range(self.vcpu_count))
            pts_base_cmd = f'phoronix-test-suite batch-run {self.benchmark_full}'
            cpu_info = f"Using all {self.vcpu_count} vCPUs (no taskset)"
        else:
            cpu_list = self.get_cpu_affinity_list(num_threads)
            pts_base_cmd = f'taskset -c {cpu_list} phoronix-test-suite batch-run {self.benchmark_full}'
            cpu_info = f"CPU affinity (taskset): {cpu_list}"

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

        # Python 3.12+ compatibility: override PYSPARK_PYTHON
        python_exec_env = ''
        if self.spark_python_exec:
            python_exec_env = (
                f'PYSPARK_PYTHON={self.spark_python_exec} '
                f'PYSPARK_DRIVER_PYTHON={self.spark_python_exec} '
            )
            print(f"[INFO] Spark Python override: {self.spark_python_exec}")

        # JDK17 + Spark 3.3 compatibility: --add-opens flags via JDK_JAVA_OPTIONS
        java_env = f'JDK_JAVA_OPTIONS="{_SPARK_JAVA_OPTS}" SPARK_JAVA_OPTS="{_SPARK_JAVA_OPTS}" '

        batch_env = (
            f'{quick_env}'
            f'{python_exec_env}'
            f'{java_env}'
            f'NUM_CPU_CORES={num_threads} '
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
        print(f"[INFO] {cpu_info}")
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

        # Failure detection
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
            f.write("Spark Benchmark Summary\n")
            f.write(f"Machine: {self.machine_name}\n")
            f.write(f"Test Category: {self.test_category}\n")
            f.write(f"Thread list: {self.thread_list}\n")
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
            "thread_list": self.thread_list,
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
        print("Apache Spark Benchmark Runner")
        print(f"{'='*80}")
        print(f"[INFO] Machine:        {self.machine_name}")
        print(f"[INFO] OS:             {self.os_name}")
        print(f"[INFO] vCPU count:     {self.vcpu_count}")
        print(f"[INFO] Test category:  {self.test_category}")
        print(f"[INFO] Thread list:    {self.thread_list}")
        print("[INFO] TH_scaling:     spark-internal (spark-defaults.conf)")
        print(f"[INFO] Results dir:    {self.results_dir}")
        if self.spark_python_exec:
            print(f"[INFO] PySpark Python: {self.spark_python_exec}")
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

        # Patch the spark wrapper for Python 3.12+ compatibility (idempotent)
        self.patch_spark_wrapper()

        # Upgrade bundled cloudpickle to 2.2.1+ for Python 3.11/3.12 compatibility.
        # CloudPickle 2.2.0 (PySpark 3.3.0) does not handle co_qualname (added in Python 3.11).
        # This replaces pyspark/cloudpickle/ inside the extracted pyspark directory,
        # which takes precedence over pyspark.zip in Spark's PYTHONPATH. (idempotent)
        self.upgrade_pyspark_cloudpickle()

        # Run benchmark for each thread count
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
        description='Apache Spark Benchmark Runner (spark-internal thread scaling)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s             # 4-point thread scaling [nproc/4, nproc/2, nproc*3/4, nproc]
  %(prog)s 8           # Fixed: run with 8 threads only
  %(prog)s --threads 8 # Same as above
  %(prog)s --quick     # Quick mode: FORCE_TIMES_TO_RUN=1
        """
    )
    parser.add_argument(
        'threads_pos', nargs='?', type=int,
        help='Thread count (optional; omit for 4-point scaling)'
    )
    parser.add_argument(
        '--threads', type=int,
        help='Run with specified thread count only'
    )
    parser.add_argument(
        '--quick', action='store_true',
        help='Quick mode: run tests once (FORCE_TIMES_TO_RUN=1) for development'
    )

    args = parser.parse_args()
    threads = args.threads if args.threads is not None else args.threads_pos

    if args.quick:
        print("[INFO] Quick mode enabled: FORCE_TIMES_TO_RUN=1")

    runner = SparkRunner(threads_arg=threads, quick_mode=args.quick)
    success = runner.run()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
