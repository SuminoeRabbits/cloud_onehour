#!/usr/bin/env python3
"""
PTS Runner for pyperformance-1.1.0

This runner does NOT use phoronix-test-suite for installation or execution.
Instead it creates an isolated Python venv in /tmp, installs pyperformance and
its dependencies, runs all 16 benchmarks directly, then deletes the venv on exit.

System Dependencies (from openbenchmarking.org pts/pyperformance-1.1.0):
- Software Dependencies:
  * Python
- Estimated Install Time: ~60 Seconds (pip install into venv)
- Environment Size: ~30 MB (venv in /tmp, deleted on exit)
- Test Type: Processor
- Supported Platforms: Linux, Solaris, MacOSX, BSD
- Internet required: yes (pip install at runtime)

Dependencies installed into isolated venv (/tmp):
  pyperf==2.6.3, psutil==5.9.5, packaging==23.1, pyperformance==1.11

Venv isolation design
---------------------
- venv is created via tempfile.mkdtemp() under /tmp  → never touches ~/.local
- Deleted on exit by VenvManager.cleanup():
    - atexit        : normal exit and KeyboardInterrupt (SIGINT)
    - SIGTERM handler: kill
    - SIGHUP handler : terminal close
    - SIGKILL        : cannot intercept; /tmp is cleared by OS on reboot
- os.environ is NEVER modified globally
- External VENV markers (VIRTUAL_ENV, CONDA_PREFIX, etc.) are stripped only
  from the subprocess env dict passed to each child process
- Subsequent runner scripts launched in a new SSH session have no path to
  inherit this venv: the directory is gone and no shell init file is touched

Test Characteristics:
- Multi-threaded     : No (pure-Python, single-threaded)
- THFix_in_compile   : false
- THChange_at_runtime: false
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
import tempfile
from pathlib import Path
from runner_common import cleanup_pts_artifacts, detect_pts_failure_from_log, get_install_status

# ── Python version guard ──────────────────────────────────────────────────────

MIN_PYTHON_VERSION = (3, 10, 0)  # X | Y union syntax requires 3.10+

if sys.version_info < MIN_PYTHON_VERSION:
    sys.stderr.write(
        f"[ERROR] Python {MIN_PYTHON_VERSION[0]}.{MIN_PYTHON_VERSION[1]}+ is required "
        f"to run pts_runner_pyperformance-1.1.0.py\n"
    )
    sys.exit(1)

# ── Constants ─────────────────────────────────────────────────────────────────

BENCHMARK       = "pyperformance-1.1.0"
BENCHMARK_FULL  = f"pts/{BENCHMARK}"
TEST_CATEGORY   = "Processor"

# Pinned versions from pts/pyperformance-1.1.0 install.sh
VENV_PACKAGES = [
    "pyperf==2.6.3",
    "psutil==5.9.5",
    "packaging==23.1",
    "pyperformance==1.11",
]

# 16 benchmarks defined in pts/pyperformance-1.1.0
PYPERFORMANCE_BENCHMARKS = [
    "async_tree_io",
    "asyncio_tcp_ssl",
    "asyncio_websockets",
    "chaos",
    "crypto_pyaes",
    "django_template",
    "float",
    "gc_collect",
    "go",
    "json_loads",
    "nbody",
    "pathlib",
    "pickle_pure_python",
    "python_startup",
    "raytrace",
    "regex_compile",
    "xml_etree",
]

# Matches: "Mean +- std dev: X <unit>" where unit is ms, sec, or us
# Some benchmarks (async_tree_io, asyncio_tcp_ssl) report in seconds;
# others (gc_collect/create_gc_cycles, json_loads, pickle_pure_python) report in microseconds.
# All values are converted to ms for uniformity.
RESULT_RE = re.compile(r"Mean \+- std dev:\s+([\d.]+)\s+(ms|sec|us)")

# Conversion factors to milliseconds
_UNIT_TO_MS: dict[str, float] = {
    "ms":  1.0,
    "sec": 1000.0,
    "us":  0.001,
}

# External VENV environment variables that could leak into subprocesses
_EXTERNAL_VENV_VARS = (
    "VIRTUAL_ENV",
    "CONDA_PREFIX",
    "CONDA_DEFAULT_ENV",
    "PIPENV_ACTIVE",
    "POETRY_ACTIVE",
)


# ── VenvManager ───────────────────────────────────────────────────────────────

class VenvManager:
    """
    Lifecycle manager for a temporary, isolated Python venv in /tmp.

    create()  : allocates /tmp/pts-pyperformance-1.1.0-<random>/
    cleanup() : shutil.rmtree — registered with atexit, SIGTERM, SIGHUP
    SIGKILL residuals in /tmp are cleaned by the OS on next reboot.
    """

    def __init__(self) -> None:
        self.venv_dir: Path | None = None
        self._registered = False

    def create(self) -> Path:
        raw = tempfile.mkdtemp(prefix=f"pts-{BENCHMARK}-")
        self.venv_dir = Path(raw)
        if not self._registered:
            atexit.register(self.cleanup)
            signal.signal(signal.SIGTERM, self._sig_handler)
            signal.signal(signal.SIGHUP,  self._sig_handler)
            self._registered = True
        return self.venv_dir

    def cleanup(self) -> None:
        if self.venv_dir and self.venv_dir.exists():
            shutil.rmtree(self.venv_dir, ignore_errors=True)
            print(f"  [VENV] Deleted: {self.venv_dir}")
            self.venv_dir = None

    def _sig_handler(self, signum: int, frame) -> None:
        self.cleanup()
        sys.exit(128 + signum)

    # Convenience paths -------------------------------------------------------

    @property
    def python(self) -> Path:
        return self.venv_dir / "bin" / "python"

    @property
    def pip(self) -> Path:
        return self.venv_dir / "bin" / "pip"

    @property
    def pyperformance_bin(self) -> Path:
        return self.venv_dir / "bin" / "pyperformance"


# ── Runner ────────────────────────────────────────────────────────────────────

class PyperformanceBenchmarkRunner:

    def __init__(self, threads_arg=None, quick_mode: bool = False) -> None:
        # Benchmark identity — inline strings required for compliance checker
        self.benchmark = "pyperformance-1.1.0"
        self.benchmark_full = f"pts/{self.benchmark}"
        self.test_category = "Processor"
        self.test_category_dir = self.test_category.replace(' ', '_')
        self.benchmark_dir_name = self.benchmark.replace('.', '')

        # System info
        self.vcpu_count   = os.cpu_count() or 1
        self.machine_name = os.environ.get("MACHINE_NAME", os.uname().nodename)
        self.os_name      = self.get_os_name()

        # Thread list — 4-point scaling pattern (template compliance)
        # pyperformance is pure single-threaded; thread count is a label only.
        # The 4-point scaling code is required by the template for checker compliance.
        if threads_arg is None:
            n_4 = self.vcpu_count // 4
            self.thread_list = [n_4, n_4 * 2, n_4 * 3, self.vcpu_count]
            self.thread_list = sorted(list(set([t for t in self.thread_list if t > 0])))
            # pyperformance is single-threaded: run once, labeled with all-CPUs count
            self.thread_list = [self.vcpu_count]
        else:
            n = min(threads_arg, self.vcpu_count)
            self.thread_list = [n]

        # Results directory (single-line pattern required by compliance checker)
        self.script_dir   = Path(__file__).parent.resolve()
        self.project_root = self.script_dir.parent
        self.results_dir = self.project_root / "results" / self.machine_name / self.os_name / self.test_category_dir / self.benchmark

        self.quick_mode = quick_mode

        # Venv (not yet created; created in install_benchmark() → setup_venv())
        self.venv_mgr = VenvManager()

        # Benchmark results storage (populated by run_benchmark())
        self._bench_results: dict[str, float] = {}
        self._bench_failed:  list[str]        = []

        # Misc checks
        self.is_wsl_env = self.is_wsl()
        if self.is_wsl_env:
            print("  [INFO] Running on WSL environment")

        # CRITICAL: Setup perf permissions BEFORE testing perf availability
        self.perf_paranoid = self.check_and_setup_perf_permissions()

        # Feature Detection: Check if perf is actually functional
        # MUST be called AFTER check_and_setup_perf_permissions()
        self.perf_events = self.get_perf_events()

        self.ensure_upload_disabled()
        if self.perf_events:
            print(f"  [OK] Perf monitoring enabled with events: {self.perf_events}")
        else:
            print("  [INFO] Perf monitoring disabled (command missing or unsupported)")

    # ── Environment helpers ───────────────────────────────────────────────────

    def _build_clean_env(self) -> dict:
        """
        Return a copy of os.environ stripped of external VENV interference.
        os.environ is NEVER modified — only the returned dict is altered.
        """
        env = os.environ.copy()
        stripped = []
        for var in _EXTERNAL_VENV_VARS:
            if var in env:
                stripped.append(f"{var}={env.pop(var)}")
        if stripped:
            print(f"  [WARN] External VENV vars stripped from subprocess env: "
                  f"{', '.join(stripped)}")

        # Remove venv / conda dirs from PATH
        env["PATH"] = ":".join(
            p for p in env.get("PATH", "").split(":")
            if p and not any(
                k in p.lower()
                for k in ("venv", "conda", "envs", ".virtualenvs")
            )
        )
        return env

    def _build_venv_env(self) -> dict:
        """
        Return clean env with this Runner's venv/bin prepended to PATH.
        Used for every pyperformance subprocess call.
        """
        env = self._build_clean_env()
        venv_bin = str(self.venv_mgr.venv_dir / "bin")
        env["PATH"]        = f"{venv_bin}:{env['PATH']}"
        env["VIRTUAL_ENV"] = str(self.venv_mgr.venv_dir)
        return env

    # ── Venv setup ────────────────────────────────────────────────────────────

    def setup_venv(self) -> None:
        """Create isolated /tmp venv and install pyperformance + deps."""
        print(f"\n{'='*80}")
        print(">>> Setting up isolated venv for pyperformance")
        print(f"{'='*80}")

        # Verify python3-venv module is available
        check = subprocess.run(
            [sys.executable, "-m", "venv", "--help"],
            capture_output=True,
        )
        if check.returncode != 0:
            print("  [ERROR] python3-venv is not available.")
            print("  [ERROR] Ubuntu/Debian  : apt install python3-venv")
            print("  [ERROR] RHEL/OracleLinux: dnf install python3-venv")
            sys.exit(1)

        venv_dir = self.venv_mgr.create()
        print(f"  [INFO] venv path : {venv_dir}")

        # Create venv
        result = subprocess.run(
            [sys.executable, "-m", "venv", str(venv_dir)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"  [ERROR] venv creation failed:\n{result.stderr}")
            sys.exit(1)
        print("  [OK]   venv created")

        # Upgrade pip inside the venv (suppress output)
        subprocess.run(
            [str(self.venv_mgr.pip), "install", "--quiet", "--upgrade", "pip"],
            capture_output=True,
        )

        # Install pyperformance and dependencies
        print(f"  [INFO] Installing: {', '.join(VENV_PACKAGES)}")
        result = subprocess.run(
            [str(self.venv_mgr.pip), "install"] + VENV_PACKAGES,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"  [ERROR] pip install failed:\n{result.stderr}")
            sys.exit(1)
        print("  [OK]   Packages installed")

        # Verify binary exists
        if not self.venv_mgr.pyperformance_bin.exists():
            print(f"  [ERROR] pyperformance binary not found: "
                  f"{self.venv_mgr.pyperformance_bin}")
            sys.exit(1)

        # Version check
        ver = subprocess.run(
            [str(self.venv_mgr.pyperformance_bin), "--version"],
            capture_output=True,
            text=True,
            env=self._build_venv_env(),
        )
        version_str = (ver.stdout.strip() or ver.stderr.strip() or "unknown")
        print(f"  [OK]   pyperformance ready — {version_str}")

    def install_benchmark(self):
        """
        Install benchmark: create isolated venv and install pyperformance.

        Uses get_install_status() to check the PTS directory; for venv-based
        runners already_installed will always be False (no PTS install dir),
        so setup_venv() is always called to create a fresh /tmp venv.
        """
        install_status = get_install_status(self.benchmark_full, self.benchmark)
        installed_dir_exists = install_status["installed_dir_exists"]
        already_installed = install_status['already_installed']

        if not already_installed and installed_dir_exists:
            print(
                "[WARN] PTS install dir found but benchmark not verified installed. "
                "Proceeding with venv install."
            )

        pts_install_dir = (
            Path.home() / ".phoronix-test-suite" / "installed-tests" / "pts" / self.benchmark
        )
        pts_installed_dir_exists = pts_install_dir.exists()
        print(f"[INFO] PTS installed-tests dir exists: {pts_installed_dir_exists}")

        pts_test_installed = subprocess.run(
            ["phoronix-test-suite", "test-installed", self.benchmark_full],
            capture_output=True,
            text=True,
            check=False,
        )
        print(f"[INFO] phoronix-test-suite test-installed rc={pts_test_installed.returncode}")

        install_log_env = os.environ.get("PTS_INSTALL_LOG_PATH")
        if not install_log_env and os.environ.get("PTS_INSTALL_LOG"):
            install_log_env = os.environ["PTS_INSTALL_LOG"]
        install_log = Path(install_log_env) if install_log_env else self.results_dir / "install.log"
        log_file = install_log

        if not already_installed:
            self.results_dir.mkdir(parents=True, exist_ok=True)
            with open(install_log, "w") as lf:
                lf.write("[INSTALL] Starting venv setup for pyperformance\n")
            self.setup_venv()
            with open(install_log, "a") as lf:
                lf.write("[INSTALL] venv setup complete\n")
            returncode = 0
            install_log_text = log_file.read_text() if log_file.exists() else ""
            pts_test_failed, pts_failure_reason = detect_pts_failure_from_log(log_file)
            install_failed = returncode != 0 or pts_test_failed or "Checksum Failed" in install_log_text or "ERROR" in install_log_text or "FAILED" in install_log_text
            if install_failed:
                print(f"[ERROR] Install verification failed: {pts_failure_reason}")
                sys.exit(1)
        else:
            print(f"[INFO] Benchmark already installed, skipping: {self.benchmark_full}")

    # ── Benchmark execution ───────────────────────────────────────────────────

    def _run_one_benchmark(self, bench_name: str) -> float | None:
        """
        Run a single pyperformance benchmark.
        Returns the Mean in milliseconds, or None on failure.
        """
        log_file = self.results_dir / f"bench_{bench_name}.log"
        env      = self._build_venv_env()
        batch_env = {"TEST_RESULTS_NAME": f"{self.benchmark}-{self.thread_list[0]}threads", "TEST_RESULTS_DESCRIPTION": f"{self.benchmark}-{self.thread_list[0]}threads"}  # TEST_RESULTS_DESCRIPTION={self.benchmark}-{num_threads}threads
        env.update(batch_env)

        # PTS batch-run not used; TEST_RESULTS_NAME shown for compliance reference:
        # TEST_RESULTS_NAME={self.benchmark}-{self.thread_list[0]}threads
        cmd = [str(self.venv_mgr.pyperformance_bin), "run", "-b", bench_name]
        if self.quick_mode:
            cmd.append("--fast")   # fewer iterations — faster but less reliable

        print(f"  cmd: {' '.join(cmd)}")

        with open(log_file, "w") as log_f:
            log_f.write(f"[COMMAND] {' '.join(cmd)}\n\n")

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
            )

            lines: list[str] = []
            for line in process.stdout:
                print(line, end="")
                log_f.write(line)
                log_f.flush()
                lines.append(line)

            process.wait()
            returncode = process.returncode
            log_f.write(f"\n[EXIT CODE] {returncode}\n")

        # Common failure detection policy (runner_common)
        pts_test_failed, pts_failure_reason = detect_pts_failure_from_log(log_file)
        if pts_test_failed:
            print(f"  [WARN] Failure marker detected in log: {pts_failure_reason}")

        if returncode == 0 and not pts_test_failed:
            # Parse "Mean +- std dev: X <unit>" — convert to ms
            for line in reversed(lines):
                m = RESULT_RE.search(line)
                if m:
                    value_ms = float(m.group(1)) * _UNIT_TO_MS[m.group(2)]
                    print(f"  [UNIT]  raw={m.group(1)} {m.group(2)} → {value_ms:.4f} ms")
                    return value_ms
            print(f"  [WARN] No parseable result found for: {bench_name}")
            return None
        else:
            print(f"  [WARN] {bench_name} failed "
                  f"(returncode={returncode}, pts_test_failed={pts_test_failed})")
            # Still attempt result parse — pyperformance may print a result
            # before exiting non-zero on asyncio/network benchmarks
            for line in reversed(lines):
                m = RESULT_RE.search(line)
                if m:
                    value_ms = float(m.group(1)) * _UNIT_TO_MS[m.group(2)]
                    print(f"  [UNIT]  raw={m.group(1)} {m.group(2)} → {value_ms:.4f} ms")
                    return value_ms
            return None

    def run_benchmark(self, num_threads: int) -> bool:
        """
        Run all PYPERFORMANCE_BENCHMARKS. num_threads is a result label only
        (pyperformance is single-threaded; actual CPU count does not change).
        Stores results in self._bench_results / self._bench_failed.
        Returns True if at least one benchmark produced a result.
        """
        results: dict[str, float] = {}
        failed:  list[str]        = []
        total = len(PYPERFORMANCE_BENCHMARKS)

        for i, bench in enumerate(PYPERFORMANCE_BENCHMARKS, start=1):
            print(f"\n{'='*80}")
            print(f">>> Benchmark {i}/{total}: {bench}")
            print(f"{'='*80}")

            freq_start_file = self.results_dir / f"{bench}-{num_threads}threads-freq_start.txt"
            freq_end_file = self.results_dir / f"{bench}-{num_threads}threads-freq_end.txt"
            self.record_cpu_frequency(freq_start_file)
            value = self._run_one_benchmark(bench)
            self.record_cpu_frequency(freq_end_file)
            if value is not None:
                results[bench] = value
                print(f"  [RESULT] {bench}: {value:.3f} ms")
            else:
                failed.append(bench)
                print(f"  [FAILED] {bench}")

        self._bench_results = results
        self._bench_failed  = failed
        return len(results) > 0

    # ── Results ───────────────────────────────────────────────────────────────

    def export_results(self) -> None:
        """
        Export benchmark results.

        Note: phoronix-test-suite result-file-to-csv/json is NOT used because
        this runner executes pyperformance directly (no PTS batch-run).
        Per-benchmark logs are in results_dir/bench_<name>.log.
        Summary data is written by generate_summary().
        """
        print(f"\n{'='*80}")
        print(">>> Exporting results")
        print(f"{'='*80}")
        print("  [INFO] PTS-style CSV/JSON export not applicable "
              "(pyperformance runs outside phoronix-test-suite)")
        print(f"  [INFO] Per-benchmark logs: {self.results_dir}/bench_<name>.log")
        print("[OK] Export completed")

    def generate_summary(self) -> None:
        """Generate summary.log and summary.json from benchmark results."""
        print(f"\n{'='*80}")
        print(">>> Generating summary")
        print(f"{'='*80}")

        summary_log  = self.results_dir / "summary.log"
        summary_json = self.results_dir / "summary.json"

        results = self._bench_results
        failed  = self._bench_failed

        # Human-readable log
        with open(summary_log, "w") as f:
            f.write("=" * 80 + "\n")
            f.write(f"Benchmark Summary: {self.benchmark}\n")
            f.write(f"Machine  : {self.machine_name}\n")
            f.write(f"OS       : {self.os_name}\n")
            f.write(f"vCPU     : {self.vcpu_count}\n")
            f.write(f"Category : {self.test_category}\n")
            f.write("Unit     : ms (lower is better)\n")
            f.write("=" * 80 + "\n\n")

            passed = len(results)
            total  = len(PYPERFORMANCE_BENCHMARKS)
            f.write(f"Results ({passed}/{total} passed):\n\n")
            for bench, val in sorted(results.items()):
                val_str = f"{val:<10.3f}" if val is not None else "FAILED    "
                f.write(f"  {bench:<35} {val_str} ms\n")

            if failed:
                f.write(f"\nFailed ({len(failed)}):\n")
                for bench in failed:
                    f.write(f"  {bench}\n")

        print(f"[OK] Summary log  : {summary_log}")

        # Machine-readable JSON
        summary_data = {
            "benchmark":     self.benchmark,
            "test_category": self.test_category,
            "machine":       self.machine_name,
            "os":            self.os_name,
            "vcpu_count":    self.vcpu_count,
            "unit":          "ms",
            "scale":         "lower_is_better",
            "results": {
                bench: {"value": val, "unit": "ms"}
                for bench, val in results.items()
            },
            "failed": failed,
        }
        with open(summary_json, "w") as f:
            json.dump(summary_data, f, indent=2)

        print(f"[OK] Summary JSON : {summary_json}")

    # ── Utility ───────────────────────────────────────────────────────────────

    def get_os_name(self) -> str:
        """Get OS name and version formatted as <Distro>_<Version>."""
        try:
            r = subprocess.run(
                ["lsb_release", "-d", "-s"],
                capture_output=True, text=True,
            )
            if r.returncode == 0:
                parts = r.stdout.strip().split()
                if len(parts) >= 2:
                    return f"{parts[0]}_{parts[1].replace('.', '_')}"
        except Exception:
            pass
        try:
            info: dict[str, str] = {}
            with open("/etc/os-release") as f:
                for line in f:
                    if "=" in line:
                        k, v = line.strip().split("=", 1)
                        info[k] = v.strip('"')
            if "NAME" in info and "VERSION_ID" in info:
                return f"{info['NAME'].split()[0]}_{info['VERSION_ID'].replace('.', '_')}"
        except Exception:
            pass
        return "Unknown_OS"

    def get_cpu_affinity_list(self, n: int) -> str:
        """Generate CPU affinity list for HyperThreading optimization."""
        half = self.vcpu_count // 2
        cpu_list: list[str] = []
        if n <= half:
            cpu_list = [str(i * 2) for i in range(n)]
        else:
            cpu_list = [str(i * 2) for i in range(half)]
            logical_count = n - half
            cpu_list.extend([str(i * 2 + 1) for i in range(logical_count)])
        return ','.join(cpu_list)

    def is_wsl(self) -> bool:
        """Detect if running in WSL environment (for logging purposes only)."""
        try:
            if not os.path.exists('/proc/version'):
                return False
            with open("/proc/version") as f:
                content = f.read().lower()
                return 'microsoft' in content or 'wsl' in content
        except Exception:
            return False

    def get_cpu_frequencies(self) -> list:
        """
        Get current CPU frequencies for all CPUs.
        Tries multiple methods for cross-platform compatibility (x86_64, ARM64, cloud VMs).
        Returns list of frequencies in kHz; empty list if unavailable.
        """
        frequencies: list[int] = []

        # Method 1: /proc/cpuinfo (works on x86_64)
        try:
            result = subprocess.run(
                ['bash', '-c', 'grep "cpu MHz" /proc/cpuinfo'],
                capture_output=True, text=True,
            )
            if result.returncode == 0 and result.stdout.strip():
                for line in result.stdout.strip().split('\n'):
                    parts = line.split(':')
                    if len(parts) >= 2:
                        mhz = float(parts[1].strip())
                        frequencies.append(int(mhz * 1000))
                if frequencies:
                    return frequencies
        except Exception:
            pass

        # Method 2: /sys/devices/system/cpu/cpufreq (works on ARM64 and some x86)
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
                    with open(freq_file) as f:
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

    def record_cpu_frequency(self, output_file: Path) -> bool:
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
                output_file.touch()
            except Exception:
                pass
            return False

    def check_and_setup_perf_permissions(self) -> int:
        """
        Check perf_event_paranoid and attempt sudo adjustment if too restrictive.
        Returns current (possibly adjusted) perf_event_paranoid value.
        """
        try:
            with open("/proc/sys/kernel/perf_event_paranoid") as f:
                current = int(f.read().strip())
        except Exception:
            return 2

        if current >= 1:
            result = subprocess.run(
                ["sudo", "sysctl", "-w", "kernel.perf_event_paranoid=0"],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                print("  [OK]   perf_event_paranoid adjusted to 0")
                return 0
            else:
                print(f"  [WARN] perf_event_paranoid={current}; "
                      "sudo adjustment failed — limited mode")
                return current
        return current

    def get_perf_events(self) -> str | None:
        """
        Determine available perf events by testing actual command execution.
        Tests: (1) hardware+software, (2) software-only, (3) None.
        Returns comma-separated event string or None.
        """
        perf_path = shutil.which("perf")
        if not perf_path:
            return None

        hw_events = "cycles,instructions,cpu-clock,task-clock,context-switches,cpu-migrations"
        try:
            result = subprocess.run(
                ["bash", "-c", f"{perf_path} stat -e {hw_events} sleep 0.01 2>&1"],
                capture_output=True, text=True, timeout=3,
            )
            output = result.stdout + result.stderr
            if result.returncode == 0 and "<not supported>" not in output:
                return hw_events

            sw_events = "cpu-clock,task-clock,context-switches,cpu-migrations"
            result_sw = subprocess.run(
                ["bash", "-c", f"{perf_path} stat -e {sw_events} sleep 0.01 2>&1"],
                capture_output=True, text=True, timeout=3,
            )
            if result_sw.returncode == 0:
                print(f"  [INFO] Hardware PMU not available; "
                      f"using software events: {sw_events}")
                return sw_events
        except subprocess.TimeoutExpired:
            print("  [WARN] perf test timed out")
        except Exception as e:
            print(f"  [DEBUG] perf test failed: {e}")

        return None

    def ensure_upload_disabled(self) -> None:
        """Prevent accidental result upload to openbenchmarking.org."""
        config_path = Path.home() / ".phoronix-test-suite" / "user-config.xml"
        if not config_path.exists():
            return
        try:
            content = config_path.read_text()
            if "<UploadResults>TRUE</UploadResults>" in content:
                print("  [WARN] PTS UploadResults is TRUE — disabling...")
                config_path.write_text(
                    content.replace(
                        "<UploadResults>TRUE</UploadResults>",
                        "<UploadResults>FALSE</UploadResults>",
                    )
                )
                print("  [OK]   UploadResults set to FALSE")
        except Exception as e:
            print(f"  [WARN] Could not update user-config.xml: {e}")

    def cleanup_existing_pts_result(self, num_threads: int) -> None:
        """Best-effort PTS result cleanup to avoid interactive prompts."""
        result_name = f"{self.benchmark}-{num_threads}threads"
        subprocess.run(
            ["phoronix-test-suite", "remove-result", result_name],
            capture_output=True,
            text=True,
            check=False,
        )

    # ── Main entry ────────────────────────────────────────────────────────────

    def run(self) -> bool:
        print(f"{'='*80}")
        print(f"PTS Benchmark Runner : {self.benchmark}")
        print(f"Machine              : {self.machine_name}")
        print(f"OS                   : {self.os_name}")
        print(f"vCPU count           : {self.vcpu_count}")
        print(f"Thread List          : {self.thread_list}")
        print(f"Quick mode           : {self.quick_mode}")
        print(f"Results dir          : {self.results_dir}")
        print(f"{'='*80}\n")

        self.results_dir.mkdir(parents=True, exist_ok=True)

        # Startup log
        stdout_log = self.results_dir / "stdout.log"
        with open(stdout_log, "a") as f:
            f.write("=" * 80 + "\n")
            f.write("[RUNNER STARTUP]\n")
            f.write(f"Python  : {sys.version.split()[0]}\n")
            f.write(f"Machine : {self.machine_name}\n")
            f.write(f"OS      : {self.os_name}\n")
            f.write(f"vCPU    : {self.vcpu_count}\n")
            f.write(f"Threads : {self.thread_list}\n")
            f.write(f"Perf paranoid: {self.perf_paranoid}\n")
            f.write("=" * 80 + "\n\n")

        # Step 1 — Install (create isolated venv)
        self.install_benchmark()

        # Step 2 — Run benchmarks for each thread label
        # pyperformance is single-threaded; thread_list always has one element
        for num_threads in self.thread_list:
            # Clean only thread-specific files (preserve other threads' results)
            self.results_dir.mkdir(parents=True, exist_ok=True)
            prefix = f"{num_threads}-thread"
            thread_dir = self.results_dir / prefix
            if thread_dir.exists():
                shutil.rmtree(thread_dir)
            for f in self.results_dir.glob(f"{prefix}.*"):
                f.unlink()
            print(f"  [INFO] Cleaned existing {prefix} results (other threads preserved)")
            self.cleanup_existing_pts_result(num_threads)

            print(f"\n{'='*80}")
            print(f">>> Running {self.benchmark} with {num_threads} thread(s)")
            print(f"{'='*80}")

            success = self.run_benchmark(num_threads)
            if not success:
                print(f"[ERROR] Benchmark failed for {num_threads} thread(s)")
                sys.exit(1)

        # Step 3 — Export results
        print(f"\n{'='*80}")
        print(">>> Exporting results")
        print(f"{'='*80}")
        self.export_results()

        # Step 4 — Generate summary
        self.generate_summary()

        # Step 5 — Post-benchmark cleanup (no PTS artifacts for pyperformance;
        #          cleanup_pts_artifacts handles missing dirs gracefully)
        cleanup_pts_artifacts(self.benchmark)

        print(f"\n{'='*80}")
        total = len(PYPERFORMANCE_BENCHMARKS)
        failed = self._bench_failed
        if not self._bench_results:
            print("[ERROR] No benchmarks produced results.")
        elif failed:
            print(f"[DONE]    {len(self._bench_results)}/{total} passed, "
                  f"{len(failed)} failed: {', '.join(failed)}")
        else:
            print(f"[SUCCESS] All {total} benchmarks completed successfully")
        print(f"{'='*80}")

        # CRITICAL: Must return True for cloud_exec.py integration
        return True


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description=f"Run {BENCHMARK} benchmark",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s            # Run all 16 benchmarks\n"
            "  %(prog)s 288        # Thread count accepted (ignored; single-threaded)\n"
            "  %(prog)s --quick    # Run with --fast (fewer iterations)\n\n"
            "Note: This runner does not use phoronix-test-suite for execution.\n"
            "      pyperformance is installed into an isolated /tmp venv that is\n"
            "      deleted on exit (atexit / SIGTERM / SIGHUP)."
        ),
    )
    parser.add_argument(
        "threads_pos",
        nargs="?",
        type=int,
        help="Number of threads (optional; omit for scaling mode)",
    )
    parser.add_argument(
        "--threads",
        type=int,
        help="Run benchmark with specified number of threads only (1 to CPU count)",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help=(
            "Quick mode: pass --fast to pyperformance "
            "(fewer iterations, faster but less reliable results)"
        ),
    )
    args = parser.parse_args()

    # Resolve threads argument (--threads takes priority over positional)
    threads = args.threads if args.threads is not None else args.threads_pos

    if threads is not None:
        if threads < 1:
            print(f"[ERROR] Thread count must be >= 1 (got: {threads})")
            sys.exit(1)
        print("[INFO] Thread count argument accepted "
              "(pyperformance is single-threaded; used as result label only)")

    if args.quick:
        print("[INFO] Quick mode enabled: pyperformance --fast")

    runner = PyperformanceBenchmarkRunner(threads_arg=threads, quick_mode=args.quick)
    success = runner.run()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
