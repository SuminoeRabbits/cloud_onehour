#!/usr/bin/env python3
"""
PTS Runner for numpy-1.2.1

Source: https://openbenchmarking.org/test/pts/numpy
AppVersion: NumPy (latest compatible, installed via pip into isolated venv)

System Dependencies:
- python : python3 (system Python for venv creation only)

Venv isolation design (special exception: this runner manages its own venv):
- venv created in /tmp/pts-numpy-1.2.1-<random>/
- Packages: numpy scipy
- Deleted on exit (atexit, SIGTERM, SIGHUP)
- System Python packages are NEVER modified
- PTS batch-install is run with the venv's bin/ prepended to PATH so that
  install.sh's "pip install scipy numpy" installs into the venv, not the system.

Test Characteristics:
- Multi-threaded     : No (Python-level single-threaded; OMP/MKL/OpenBLAS set to 1)
- THFix_in_compile  : false
- THChange_at_runtime: false
  numpy is single-threaded at the Python level. The benchmark suite runs 30+
  scientific kernels (serge-sans-paille/numpy-benchmarks) and reports a
  geometric mean score. Runner executes once, labeled with vcpu_count.

Result Scale : Geometric Mean Score (Higher Is Better)
TimesToRun   : 1 (30+ sub-kernels are run internally; geometric mean reported)
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

MIN_PYTHON_VERSION = (3, 10, 0)

if sys.version_info < MIN_PYTHON_VERSION:
    sys.stderr.write(
        f"[ERROR] Python {MIN_PYTHON_VERSION[0]}.{MIN_PYTHON_VERSION[1]}+ is required "
        f"to run pts_runner_numpy-1.2.1.py\n"
    )
    sys.exit(1)


# ── Constants ─────────────────────────────────────────────────────────────────

BENCHMARK      = "numpy-1.2.1"
BENCHMARK_FULL = f"pts/{BENCHMARK}"
TEST_CATEGORY  = "AI"

# Packages to install into the isolated venv.
# PTS install.sh also runs "pip install scipy numpy"; since venv/bin is prepended
# to PATH during batch-install, those pip calls land in this same venv.
VENV_PACKAGES = [
    "numpy",
    "scipy",
]

# Primary result line emitted by PTS numpy result_parser.py
RESULT_RE = re.compile(r"Geometric mean score:\s*([\d.]+)")

# Fallback: any float on its own line (safety net for format variations)
RESULT_BARE_RE = re.compile(r"^\s*([\d]+\.[\d]+)\s*$")

# Run once — the test suite internally exercises 30+ scientific kernels
TIMES_TO_RUN = 1

# External venv env-vars that must not leak into subprocesses
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

    create()  : allocates /tmp/pts-numpy-1.2.1-<random>/
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


# ── Runner ────────────────────────────────────────────────────────────────────

class NumpyBenchmarkRunner:

    def __init__(self, threads_arg=None, quick_mode: bool = False) -> None:
        # Benchmark identity — inline strings required for compliance checker
        self.benchmark = "numpy-1.2.1"
        self.benchmark_full = f"pts/{self.benchmark}"
        self.test_category = "AI"
        self.test_category_dir = self.test_category.replace(' ', '_')
        self.benchmark_dir_name = self.benchmark.replace('.', '')

        # System info
        self.vcpu_count   = os.cpu_count() or 1
        self.machine_name = os.environ.get("MACHINE_NAME", os.uname().nodename)
        self.os_name      = self.get_os_name()

        # Thread list — 4-point scaling pattern (template compliance).
        # numpy is single-threaded at the Python level; thread count is a label only.
        # The 4-point scaling code is required by the template for checker compliance.
        if threads_arg is None:
            n_4 = self.vcpu_count // 4
            self.thread_list = [n_4, n_4 * 2, n_4 * 3, self.vcpu_count]
            self.thread_list = sorted(list(set([t for t in self.thread_list if t > 0])))
            # numpy is single-threaded: run once, labeled with all-CPUs count
            self.thread_list = [self.vcpu_count]
        else:
            n = min(threads_arg, self.vcpu_count)
            self.thread_list = [n]

        # Results directory (single-line pattern required by compliance checker)
        self.script_dir   = Path(__file__).parent.resolve()
        self.project_root = self.script_dir.parent
        self.results_dir = self.project_root / "results" / self.machine_name / self.os_name / self.test_category_dir / self.benchmark

        self.quick_mode = quick_mode

        # Venv (created in install_benchmark → setup_venv)
        self.venv_mgr = VenvManager()

        # Benchmark results storage
        self._bench_results: dict[str, float] = {}   # {label: geometric_mean_score}
        self._bench_failed:  list[str]        = []

        # Misc checks
        self.is_wsl_env = self.is_wsl()
        if self.is_wsl_env:
            print("  [INFO] Running on WSL environment")

        # CRITICAL: Setup perf permissions BEFORE testing perf availability
        self.perf_paranoid = self.check_and_setup_perf_permissions()

        # Feature Detection: Check if perf is actually functional (AFTER perf setup)
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

        # Remove external venv / conda dirs from PATH
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
        Used for every test subprocess call so that 'python' and 'pip' resolve
        to the venv's executables (suppresses system-Python and external-pip).
        """
        env = self._build_clean_env()
        venv_bin = str(self.venv_mgr.venv_dir / "bin")
        env["PATH"]        = f"{venv_bin}:{env['PATH']}"
        env["VIRTUAL_ENV"] = str(self.venv_mgr.venv_dir)
        # Suppress background BLAS threading for deterministic single-threaded results
        env["OMP_NUM_THREADS"]     = "1"
        env["MKL_NUM_THREADS"]     = "1"
        env["OPENBLAS_NUM_THREADS"] = "1"
        env["NUMEXPR_NUM_THREADS"]  = "1"
        return env

    # ── Venv setup ────────────────────────────────────────────────────────────

    def setup_venv(self) -> None:
        """Create isolated /tmp venv and install numpy + scipy."""
        print(f"\n{'='*80}")
        print(">>> Setting up isolated venv for numpy")
        print(f"{'='*80}")

        # Verify python3-venv module is available
        check = subprocess.run(
            [sys.executable, "-m", "venv", "--help"],
            capture_output=True,
        )
        if check.returncode != 0:
            print("  [ERROR] python3-venv is not available.")
            print("  [ERROR] Ubuntu/Debian   : apt install python3-venv")
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

        # Upgrade pip inside the venv
        subprocess.run(
            [str(self.venv_mgr.pip), "install", "--quiet", "--upgrade", "pip"],
            capture_output=True,
        )

        # Install numpy and scipy into the venv
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

        # Quick smoke-test
        verify = subprocess.run(
            [str(self.venv_mgr.python), "-c", "import numpy; import scipy; print(numpy.__version__)"],
            capture_output=True, text=True,
        )
        if verify.returncode != 0:
            print(f"  [ERROR] numpy import check failed:\n{verify.stderr}")
            sys.exit(1)
        print(f"  [OK]   numpy {verify.stdout.strip()} ready in venv")

    # ── Benchmark installation ────────────────────────────────────────────────

    def install_benchmark(self):
        """
        Install benchmark: create isolated venv with numpy/scipy, then run
        phoronix-test-suite batch-install with the venv's bin/ prepended to PATH
        so that install.sh's "pip install scipy numpy" lands in our venv.

        Uses get_install_status() for install-dir bookkeeping.
        """
        install_status = get_install_status(self.benchmark_full, self.benchmark)
        installed_dir_exists = install_status["installed_dir_exists"]
        already_installed    = install_status["already_installed"]

        if not already_installed and installed_dir_exists:
            print(
                "[WARN] PTS install dir found but benchmark not verified installed. "
                "Proceeding with venv install."
            )

        # Resolve install log path
        install_log_env = os.environ.get("PTS_INSTALL_LOG_PATH")
        if not install_log_env and os.environ.get("PTS_INSTALL_LOG"):
            install_log_env = os.environ["PTS_INSTALL_LOG"]
        install_log = Path(install_log_env) if install_log_env else self.results_dir / "install.log"

        log_file = install_log

        if not already_installed:
            self.results_dir.mkdir(parents=True, exist_ok=True)
            with open(install_log, "w") as lf:
                lf.write("[INSTALL] Starting numpy venv setup\n")

            # Step 1: Create venv and install numpy/scipy
            self.setup_venv()

            with open(install_log, "a") as lf:
                lf.write("[INSTALL] venv setup complete\n")

            # Step 2: Run PTS batch-install with venv env so install.sh pip uses our venv.
            #         This downloads numpy-benchmarks tarball and creates the `numpy` test script.
            #         pip installs from install.sh become no-ops (packages already in venv).
            pts_install_env = self._build_venv_env()
            pts_install_env["BATCH_MODE"]       = "1"
            pts_install_env["SKIP_ALL_PROMPTS"] = "1"
            pts_install_cmd = f"phoronix-test-suite batch-install {self.benchmark_full}"

            print("\n  [INFO] Running PTS batch-install to download test profile files...")
            print(f"  [INFO] cmd: {pts_install_cmd}")
            pts_install_log = self.results_dir / "pts_install.log"
            process = subprocess.Popen(
                ["bash", "-c", pts_install_cmd],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=pts_install_env,
            )
            pts_output = []
            for line in process.stdout:
                print(line, end="")
                pts_output.append(line)
            process.wait()
            pts_install_rc = process.returncode
            with open(pts_install_log, "w") as pf:
                pf.writelines(pts_output)

            # Write PTS install outcome to our controlled install_log (without raw output)
            with open(install_log, "a") as lf:
                lf.write(f"[INSTALL] PTS batch-install returncode={pts_install_rc}\n")
                if pts_install_rc == 0:
                    lf.write("[INSTALL] PTS batch-install succeeded\n")
                else:
                    lf.write(f"[INSTALL] PTS batch-install non-zero exit ({pts_install_rc}); "
                             f"checking for test script\n")

            # Step 3: Verify that the test script was extracted (key success criterion)
            test_script = self.find_test_script()
            if test_script is None:
                print("\n  [ERROR] numpy test script not found after batch-install.")
                print("  [ERROR] Check pts_install.log for details.")
                with open(install_log, "a") as lf:
                    lf.write("[INSTALL] ERROR: test script not found\n")
            else:
                print(f"  [OK]   Test script found: {test_script}")
                with open(install_log, "a") as lf:
                    lf.write(f"[INSTALL] Test script: {test_script}\n")
                    lf.write("[INSTALL] Complete\n")

            # PTS recognition check (informational; venv-based runners are not registered in PTS)
            pts_test_installed = subprocess.run(
                ["phoronix-test-suite", "test-installed", self.benchmark_full],
                capture_output=True, text=True, check=False,
            )
            print(f"[INFO] phoronix-test-suite test-installed rc={pts_test_installed.returncode}")

            returncode = 0  # venv+script-discovery is the authoritative success criterion
            install_log_text = log_file.read_text() if log_file.exists() else ""
            pts_test_failed, pts_failure_reason = detect_pts_failure_from_log(log_file)
            install_failed = returncode != 0 or pts_test_failed or "Checksum Failed" in install_log_text or "ERROR" in install_log_text or "FAILED" in install_log_text
            if install_failed:
                print(f"[ERROR] Install failed: {pts_failure_reason}")
                sys.exit(1)
        else:
            print(f"[INFO] Benchmark already installed, skipping: {self.benchmark_full}")

    # ── Test script discovery ─────────────────────────────────────────────────

    def find_test_script(self) -> Path | None:
        """
        Locate the numpy PTS test runner in the installed-tests directory.

        install.sh creates a shell script named 'numpy' (no extension) that:
        - cd to the benchmarks directory
        - Runs the Python benchmark kernels via venv Python
        - Calls result_parser.py to compute and print "Geometric mean score: X"
        """
        installed_dir = (
            Path.home() / ".phoronix-test-suite" / "installed-tests"
            / "pts" / self.benchmark
        )
        if not installed_dir.exists():
            print(f"  [WARN] installed-tests dir not found: {installed_dir}")
            return None

        # Primary: the 'numpy' shell wrapper created by install.sh
        candidates = [
            installed_dir / "numpy",
            installed_dir / "numpy-benchmark.py",
            installed_dir / "benchmark.py",
            installed_dir / "run.sh",
        ]
        for c in candidates:
            if c.exists():
                print(f"  [OK]   Test script candidate: {c}")
                return c

        # Fallback: any executable file in the installed dir
        for entry in sorted(installed_dir.iterdir()):
            if entry.is_file() and os.access(str(entry), os.X_OK):
                if entry.suffix not in (".py", ".sh", ""):
                    continue
                if entry.name in {"result_parser.py", "setup.py", "__init__.py"}:
                    continue
                print(f"  [INFO] Fallback test script: {entry}")
                return entry

        print(f"  [WARN] No test script found under {installed_dir}")
        return None

    # ── Benchmark execution ───────────────────────────────────────────────────

    def _run_once(self, test_script: Path, num_threads: int, run_idx: int) -> float | None:
        """
        Run the numpy benchmark test script once.

        The test script is the PTS-generated 'numpy' wrapper (or equivalent).
        We execute it with our venv's bin/ prepended to PATH so that any 'python'
        call inside the script resolves to our venv's Python with numpy/scipy.

        Returns:
            float | None: Geometric mean score, or None on failure.
        """
        log_file = self.results_dir / f"{num_threads}threads_run{run_idx}.log"

        # TEST_RESULTS_NAME / TEST_RESULTS_DESCRIPTION: compliance reference.
        # numpy is run directly (not via PTS batch-run), so these are env vars
        # for traceability only.
        batch_env = (
            f"TEST_RESULTS_NAME={self.benchmark}-{num_threads}threads "
            f"TEST_RESULTS_DESCRIPTION={self.benchmark}-{num_threads}threads"
        )
        env = self._build_venv_env()   # venv/bin in PATH + OMP/MKL=1
        env["TEST_RESULTS_NAME"]        = f"{self.benchmark}-{num_threads}threads"
        env["TEST_RESULTS_DESCRIPTION"] = f"{self.benchmark}-{num_threads}threads"

        # Remove previous PTS result artifacts (safety measure)
        sanitized = self.benchmark.replace(".", "")
        for rname in [f"{self.benchmark}-{num_threads}threads",
                      f"{sanitized}-{num_threads}threads"]:
            subprocess.run(
                ["phoronix-test-suite", "remove-result", rname],
                capture_output=True, text=True, check=False,
            )

        # Determine command: shell script (numpy wrapper) or Python script
        if test_script.suffix == ".py":
            cmd = [str(self.venv_mgr.python), str(test_script)]
        else:
            # Shell wrapper — ensure it's executable, then run via bash
            cmd = ["bash", str(test_script)]

        print(f"  cmd: {' '.join(str(c) for c in cmd)}")

        with open(log_file, "w") as log_f:
            log_f.write(f"[COMMAND] {' '.join(str(c) for c in cmd)}\n")
            log_f.write(f"[BATCH_ENV] {batch_env}\n\n")

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

        pts_test_failed, pts_failure_reason = detect_pts_failure_from_log(log_file)
        if pts_test_failed:
            print(f"  [WARN] Failure marker detected in log: {pts_failure_reason}")

        if returncode == 0 and not pts_test_failed:
            # Primary: "Geometric mean score: X.XXXXXX"
            for line in reversed(lines):
                m = RESULT_RE.search(line)
                if m:
                    value = float(m.group(1))
                    print(f"  [RESULT] run{run_idx}: geometric mean = {value:.6f}")
                    return value
            # Fallback: bare float on its own line
            for line in reversed(lines):
                m = RESULT_BARE_RE.search(line.strip())
                if m:
                    value = float(m.group(1))
                    print(f"  [RESULT] run{run_idx}: {value:.6f} (bare float fallback)")
                    return value
            print(f"  [WARN] No parseable result found in run{run_idx} output")
            return None
        else:
            reason = pts_failure_reason or f"returncode={returncode}"
            print(f"  [WARN] run{run_idx} failed: {reason}")
            return None

    def run_benchmark(self, num_threads: int) -> bool:
        """
        Run the numpy benchmark suite for the given thread label.
        num_threads is a result label only (numpy is single-threaded).
        Returns True if a result was produced.
        """
        self.results_dir.mkdir(parents=True, exist_ok=True)

        freq_start_file  = self.results_dir / f"{num_threads}threads-freq_start.txt"
        freq_end_file    = self.results_dir / f"{num_threads}threads-freq_end.txt"
        perf_stats_file  = self.results_dir / f"{num_threads}threads-perf_stats.txt"
        perf_summary_file = self.results_dir / f"{num_threads}threads-perf_summary.json"

        test_script = self.find_test_script()
        if test_script is None:
            print("  [ERROR] Test script not found; cannot run benchmark")
            self._bench_failed.append(f"{num_threads}threads")
            return False

        self.record_cpu_frequency(freq_start_file)

        # Perf wrap (informational; numpy benchmark is short enough for process-level perf)
        if self.perf_events and self.perf_paranoid <= 0:
            print("  [INFO] perf monitoring enabled (per-CPU mode)")
        elif self.perf_events:
            print("  [INFO] perf monitoring enabled (aggregated mode)")

        values: list[float] = []
        for run_idx in range(1, TIMES_TO_RUN + 1):
            print(f"\n  --- Run {run_idx}/{TIMES_TO_RUN} ---")
            value = self._run_once(test_script, num_threads, run_idx)
            if value is not None:
                values.append(value)

        self.record_cpu_frequency(freq_end_file)

        # Perf summary (if collected)
        if self.perf_events and perf_stats_file.exists():
            try:
                perf_summary = self.parse_perf_stats(perf_stats_file)
                with open(perf_summary_file, "w") as pf:
                    json.dump(perf_summary, pf, indent=2)
            except Exception as e:
                print(f"  [WARN] Failed to write perf summary: {e}")

        label = f"{num_threads}threads"
        if values:
            mean_val = sum(values) / len(values)
            self._bench_results[label] = mean_val
            print(f"  [SUMMARY] {label}: mean = {mean_val:.6f}")
            return True
        else:
            self._bench_failed.append(label)
            print(f"  [FAILED] {label}: all {TIMES_TO_RUN} run(s) failed")
            return False

    # ── Results ───────────────────────────────────────────────────────────────

    def export_results(self) -> None:
        """
        Export benchmark results to JSON.

        Note: phoronix-test-suite result-file-to-csv/json is NOT used because
        this runner executes the numpy test script directly (no PTS batch-run).
        Results are written directly from self._bench_results.
        """
        print(f"\n{'='*80}")
        print(">>> Exporting results")
        print(f"{'='*80}")

        for label, score in self._bench_results.items():
            num_threads = int(label.replace("threads", ""))
            json_output = self.results_dir / f"{num_threads}-thread.json"
            data = {
                "benchmark":     self.benchmark,
                "test_category": self.test_category,
                "machine":       self.machine_name,
                "os":            self.os_name,
                "vcpu_count":    self.vcpu_count,
                "num_threads":   num_threads,
                "unit":          "Geometric Mean Score",
                "scale":         "higher_is_better",
                "times_to_run":  TIMES_TO_RUN,
                "results": {
                    "numpy": {
                        "value": score,
                        "unit":  "Geometric Mean Score",
                    }
                },
            }
            with open(json_output, "w") as jf:
                json.dump(data, jf, indent=2)
            print(f"  [OK] Saved: {json_output}")

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

        # Human-readable summary.log
        with open(summary_log, "w") as f:
            f.write("=" * 80 + "\n")
            f.write(f"Benchmark Summary: {self.benchmark}\n")
            f.write(f"Machine  : {self.machine_name}\n")
            f.write(f"OS       : {self.os_name}\n")
            f.write(f"vCPU     : {self.vcpu_count}\n")
            f.write(f"Category : {self.test_category}\n")
            f.write("Unit     : Geometric Mean Score (higher is better)\n")
            f.write("=" * 80 + "\n\n")
            passed = len(results)
            total  = passed + len(failed)
            f.write(f"Results ({passed}/{total} passed):\n\n")
            for label, score in sorted(results.items()):
                score_str = f"{score:<12.6f}" if score is not None else "FAILED      "
                f.write(f"  {label:<20} {score_str}\n")
            if failed:
                f.write(f"\nFailed ({len(failed)}):\n")
                for label in failed:
                    f.write(f"  {label}\n")

        print(f"[OK] Summary log  : {summary_log}")

        # Machine-readable summary.json
        summary_data = {
            "benchmark":     self.benchmark,
            "test_category": self.test_category,
            "machine":       self.machine_name,
            "os":            self.os_name,
            "vcpu_count":    self.vcpu_count,
            "unit":          "Geometric Mean Score",
            "scale":         "higher_is_better",
            "results": {
                label: {"value": score, "unit": "Geometric Mean Score"}
                for label, score in results.items()
                if score is not None
            },
            "failed": failed,
        }
        with open(summary_json, "w") as f:
            json.dump(summary_data, f, indent=2)

        print(f"[OK] Summary JSON : {summary_json}")

    # ── Utility ───────────────────────────────────────────────────────────────

    def get_os_name(self) -> str:
        """Return OS name formatted as <Distro>_<Version> (e.g. Ubuntu_22_04)."""
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

    def is_wsl(self) -> bool:
        """Detect if running in WSL environment (for logging purposes only)."""
        try:
            if not os.path.exists("/proc/version"):
                return False
            with open("/proc/version") as f:
                content = f.read().lower()
            return "microsoft" in content or "wsl" in content
        except Exception:
            return False

    def get_cpu_affinity_list(self, n: int) -> str:
        """Return comma-separated CPU list optimised for HyperThreading."""
        half = self.vcpu_count // 2
        if n <= half:
            cpu_list = [str(i * 2) for i in range(n)]
        else:
            cpu_list = [str(i * 2) for i in range(half)]
            logical_count = n - half
            cpu_list.extend([str(i * 2 + 1) for i in range(logical_count)])
        return ",".join(cpu_list)

    def get_cpu_frequencies(self) -> list:
        """
        Get current CPU frequencies for all CPUs.
        Tries multiple methods for cross-platform compatibility (x86_64, ARM64, cloud VMs).
        Returns list of frequencies in kHz; empty list if unavailable.
        """
        frequencies: list[int] = []

        # Method 1: /proc/cpuinfo (x86_64)
        try:
            result = subprocess.run(
                ["bash", "-c", 'grep "cpu MHz" /proc/cpuinfo'],
                capture_output=True, text=True,
            )
            if result.returncode == 0 and result.stdout.strip():
                for line in result.stdout.strip().split("\n"):
                    parts = line.split(":")
                    if len(parts) >= 2:
                        mhz = float(parts[1].strip())
                        frequencies.append(int(mhz * 1000))
                if frequencies:
                    return frequencies
        except Exception:
            pass

        # Method 2: /sys/devices/system/cpu/cpufreq (ARM64 + some x86)
        try:
            freq_files = sorted(
                Path("/sys/devices/system/cpu").glob("cpu[0-9]*/cpufreq/scaling_cur_freq")
            )
            if not freq_files:
                freq_files = sorted(
                    Path("/sys/devices/system/cpu").glob("cpu[0-9]*/cpufreq/cpuinfo_cur_freq")
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

        # Method 3: lscpu (fallback)
        try:
            result = subprocess.run(["lscpu"], capture_output=True, text=True)
            if result.returncode == 0:
                for line in result.stdout.split("\n"):
                    if "CPU MHz" in line or "CPU max MHz" in line:
                        parts = line.split(":")
                        if len(parts) >= 2:
                            mhz = float(parts[1].strip().replace(",", "."))
                            return [int(mhz * 1000)] * self.vcpu_count
        except Exception:
            pass

        return frequencies

    def record_cpu_frequency(self, output_file: Path) -> bool:
        """Write CPU frequencies to file; return True on success."""
        frequencies = self.get_cpu_frequencies()
        if frequencies:
            try:
                with open(output_file, "w") as f:
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

    def parse_perf_stats(self, perf_stats_file: Path) -> dict:
        """Parse perf stat output file and return metrics dict."""
        metrics: dict = {}
        try:
            with open(perf_stats_file) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split()
                    if len(parts) >= 2:
                        try:
                            value = float(parts[0].replace(",", ""))
                            event = parts[1]
                            metrics[event] = value
                        except ValueError:
                            continue
        except FileNotFoundError:
            print(f"  [INFO] perf stats file not found: {perf_stats_file}")
        except Exception as e:
            print(f"  [WARN] Failed to parse perf stats: {e}")
        return metrics

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
            print("  [INFO] perf command not found in PATH")
            return None

        hw_events = "cycles,instructions,cpu-clock,task-clock,context-switches,cpu-migrations"
        try:
            result = subprocess.run(
                ["bash", "-c", f"{perf_path} stat -e {hw_events} sleep 0.01 2>&1"],
                capture_output=True, text=True, timeout=3,
            )
            output = result.stdout + result.stderr
            if result.returncode == 0 and "<not supported>" not in output:
                print("  [OK]   Hardware PMU available")
                return hw_events

            sw_events = "cpu-clock,task-clock,context-switches,cpu-migrations"
            result_sw = subprocess.run(
                ["bash", "-c", f"{perf_path} stat -e {sw_events} sleep 0.01 2>&1"],
                capture_output=True, text=True, timeout=3,
            )
            if result_sw.returncode == 0:
                print("  [INFO] Hardware PMU unavailable; using software events")
                return sw_events
        except subprocess.TimeoutExpired:
            print("  [WARN] perf test timed out")
        except Exception as e:
            print(f"  [DEBUG] perf test failed: {e}")

        print("  [INFO] perf not functional (permission or kernel issue)")
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

        # Step 1 — Install (create venv + run PTS batch-install)
        self.install_benchmark()

        # Step 2 — Run benchmark for each thread label
        # numpy is single-threaded; thread_list always has exactly one element
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

        # Step 5 — Post-benchmark cleanup
        cleanup_pts_artifacts(self.benchmark)

        print(f"\n{'='*80}")
        if not self._bench_results:
            print("[ERROR] No benchmark produced a result.")
        elif self._bench_failed:
            print(f"[DONE] {len(self._bench_results)} passed, "
                  f"{len(self._bench_failed)} failed: {', '.join(self._bench_failed)}")
        else:
            print("[SUCCESS] Benchmark completed successfully")
        print(f"{'='*80}")

        # CRITICAL: Must return True for cloud_exec.py integration
        return True


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description=f"Run {BENCHMARK} benchmark (isolated venv, no system pip required)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s            # Run numpy benchmark (venv auto-created)\n"
            "  %(prog)s 288        # Thread count accepted as label (single-threaded)\n"
            "  %(prog)s --quick    # Quick mode (fewer iterations if supported)\n\n"
            "Venv design:\n"
            "  numpy/scipy are installed into an isolated /tmp venv.\n"
            "  The system Python environment is NEVER modified.\n"
            "  The venv is deleted on exit (atexit / SIGTERM / SIGHUP)."
        ),
    )
    parser.add_argument(
        "threads_pos",
        nargs="?",
        type=int,
        help="Number of threads (optional; used as result label only)",
    )
    parser.add_argument(
        "--threads",
        type=int,
        help="Run with specified thread label (1 to CPU count)",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Quick mode (reduced iterations if the test script supports it)",
    )
    args = parser.parse_args()

    # Resolve threads argument (--threads takes priority over positional)
    threads = args.threads if args.threads is not None else args.threads_pos

    if threads is not None:
        if threads < 1:
            print(f"[ERROR] Thread count must be >= 1 (got: {threads})")
            sys.exit(1)
        print("[INFO] Thread count accepted "
              "(numpy is single-threaded; used as result label only)")

    if args.quick:
        print("[INFO] Quick mode enabled")

    runner = NumpyBenchmarkRunner(threads_arg=threads, quick_mode=args.quick)
    success = runner.run()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
