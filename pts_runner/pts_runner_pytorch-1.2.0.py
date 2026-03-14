#!/usr/bin/env python3
"""
PTS Runner for pytorch-1.2.0

Source: https://openbenchmarking.org/test/pts/pytorch
AppVersion: PyTorch 2.6

System Dependencies (from upstream PTS profile):
- python : python3
- pip3   : required by the upstream profile

Venv isolation design (special exception: this runner manages its own venv):
- venv created in /tmp/pts-pytorch-1.2.0-<random>/
- Python: system default interpreter used to launch the runner
- Packages: setuptools, torch==2.6.0, torchvision==0.21.0,
  torchaudio==2.6.0, pytorch-benchmark==0.3.6
- Deleted on exit (atexit, SIGTERM, SIGHUP)
- System Python packages are NEVER modified

Test Characteristics:
- Multi-threaded     : Yes (torch intra-op threads controlled at runtime)
- THFix_in_compile   : false
- THChange_at_runtime: true
  Thread count is injected via torch.set_num_threads() and common BLAS/OpenMP
  environment variables.

Default test matrix:
- Device    : CPU only
- BatchSize : 1, 16, 32, 64, 256, 512
- Model     : resnet50, resnet152, efficientnet_v2_l
- Result    : batches/sec (higher is better)
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
import textwrap
from pathlib import Path

from runner_common import cleanup_pts_artifacts, detect_pts_failure_from_log, get_install_status, get_pts_profile_dir


BENCHMARK = "pytorch-1.2.0"
BENCHMARK_FULL = f"pts/{BENCHMARK}"
TEST_CATEGORY = "AI"
# Pinned packages — single source of truth embedded in the runner.
# The runner writes REQUIREMENTS_FILE at runtime; no static file needed in git.
_PINNED_PACKAGES = (
    "setuptools\n"
    "torch==2.6.0\n"
    "torchvision==0.21.0\n"
    "torchaudio==2.6.0\n"
    "pytorch-benchmark==0.3.6\n"
)
REQUIREMENTS_FILE = Path(__file__).with_name("requirements_pytorch-1.2.0.txt")

DEFAULT_DEVICE = "cpu"
VALID_BATCH_SIZES = (1, 16, 32, 64, 256, 512)
VALID_MODELS = ("resnet50", "resnet152", "efficientnet_v2_l")

TIMES_TO_RUN = 3
QUICK_TIMES_TO_RUN = 1
INTERNAL_BENCH_RUNS = 1000
QUICK_INTERNAL_BENCH_RUNS = 100

RESULT_MEAN_RE = re.compile(r"batches_per_second_mean:\s*([0-9.]+)")
RESULT_MAX_RE = re.compile(r"batches_per_second_max:\s*([0-9.]+)")
RESULT_MIN_RE = re.compile(r"batches_per_second_min:\s*([0-9.]+)")

_EXTERNAL_VENV_VARS = (
    "VIRTUAL_ENV",
    "CONDA_PREFIX",
    "CONDA_DEFAULT_ENV",
    "PIPENV_ACTIVE",
    "POETRY_ACTIVE",
)


class VenvManager:
    """Lifecycle manager for a temporary isolated Python venv in /tmp."""

    def __init__(self) -> None:
        self.venv_dir: Path | None = None
        self._registered = False

    def create(self) -> Path:
        raw = tempfile.mkdtemp(prefix=f"pts-{BENCHMARK}-")
        self.venv_dir = Path(raw)
        if not self._registered:
            atexit.register(self.cleanup)
            signal.signal(signal.SIGTERM, self._sig_handler)
            signal.signal(signal.SIGHUP, self._sig_handler)
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

    @property
    def python(self) -> Path:
        return self.venv_dir / "bin" / "python"

    @property
    def pip(self) -> Path:
        return self.venv_dir / "bin" / "pip"


class PyTorchBenchmarkRunner:
    def __init__(
        self,
        threads_arg: int | None = None,
        quick_mode: bool = False,
    ) -> None:
        self.benchmark = "pytorch-1.2.0"
        self.benchmark_full = BENCHMARK_FULL
        self.test_category = TEST_CATEGORY
        self.test_category_dir = self.test_category.replace(" ", "_")

        self.vcpu_count = os.cpu_count() or 1
        self.machine_name = os.environ.get("MACHINE_NAME", os.uname().nodename)
        self.os_name = self.get_os_name()

        if threads_arg is None:
            n_4 = self.vcpu_count // 4
            self.thread_list = [n_4, n_4 * 2, n_4 * 3, self.vcpu_count]
            self.thread_list = sorted(list(set([t for t in self.thread_list if t > 0])))
        else:
            self.thread_list = [min(threads_arg, self.vcpu_count)]

        self.quick_mode = quick_mode
        self.times_to_run = QUICK_TIMES_TO_RUN if quick_mode else TIMES_TO_RUN
        self.internal_runs = QUICK_INTERNAL_BENCH_RUNS if quick_mode else INTERNAL_BENCH_RUNS

        self.device = DEFAULT_DEVICE
        self.batch_sizes = VALID_BATCH_SIZES
        self.models = VALID_MODELS
        self.workloads = [(model, batch_size) for model in self.models for batch_size in self.batch_sizes]
        self.venv_python = sys.executable
        self.venv_mgr = VenvManager()

        self.script_dir = Path(__file__).parent.resolve()
        self.project_root = self.script_dir.parent
        self.results_dir = self.project_root / "results" / self.machine_name / self.os_name / self.test_category_dir / self.benchmark

        self.profile_dir = get_pts_profile_dir(self.benchmark_full)

        self._bench_results: dict[str, dict[str, float]] = {}
        self._bench_failed: list[str] = []

        self.is_wsl_env = self.is_wsl()
        if self.is_wsl_env:
            print("  [INFO] Running on WSL environment")

        self.perf_paranoid = self.check_and_setup_perf_permissions()
        self.perf_events = self.get_perf_events()
        self.ensure_upload_disabled()
        if self.perf_events:
            print(f"  [OK] Perf monitoring enabled with events: {self.perf_events}")
        else:
            print("  [INFO] Perf monitoring disabled (command missing or unsupported)")

    def _build_clean_env(self) -> dict:
        env = os.environ.copy()
        stripped = []
        for var in _EXTERNAL_VENV_VARS:
            if var in env:
                stripped.append(f"{var}={env.pop(var)}")
        if stripped:
            print(f"  [WARN] External VENV vars stripped from subprocess env: {', '.join(stripped)}")
        env["PATH"] = ":".join(
            p for p in env.get("PATH", "").split(":")
            if p and not any(k in p.lower() for k in ("venv", "conda", "envs", ".virtualenvs"))
        )
        return env

    def _build_venv_env(self, num_threads: int = 1) -> dict:
        env = self._build_clean_env()
        venv_bin = str(self.venv_mgr.venv_dir / "bin")
        env["PATH"] = f"{venv_bin}:{env['PATH']}"
        env["VIRTUAL_ENV"] = str(self.venv_mgr.venv_dir)
        env["OMP_NUM_THREADS"] = str(num_threads)
        env["MKL_NUM_THREADS"] = str(num_threads)
        env["OPENBLAS_NUM_THREADS"] = str(num_threads)
        env["NUMEXPR_NUM_THREADS"] = str(num_threads)
        env["TORCH_NUM_THREADS"] = str(num_threads)
        # PTS prompt suppression (avoids interactive prompts):
        # TEST_RESULTS_NAME={self.benchmark}-{num_threads}threads
        # TEST_RESULTS_DESCRIPTION={self.benchmark}-{num_threads}threads
        env["TEST_RESULTS_NAME"] = f"{self.benchmark}-{num_threads}threads"
        env["TEST_RESULTS_DESCRIPTION"] = f"{self.benchmark}-{num_threads}threads"
        return env

    def setup_venv(self) -> None:
        print(f"\n{'=' * 80}")
        print(">>> Setting up isolated venv for pytorch")
        print(f"{'=' * 80}")
        print(f"  [INFO] venv interpreter : {self.venv_python}")

        check = subprocess.run([self.venv_python, "-m", "venv", "--help"], capture_output=True)
        if check.returncode != 0:
            print("  [ERROR] python3-venv is not available.")
            print("  [ERROR] Ubuntu/Debian   : apt install python3-venv")
            print("  [ERROR] RHEL/OracleLinux: dnf install python3-venv")
            sys.exit(1)

        venv_dir = self.venv_mgr.create()
        print(f"  [INFO] venv path : {venv_dir}")

        # Generate requirements file from embedded constant (no static file needed in git)
        REQUIREMENTS_FILE.write_text(_PINNED_PACKAGES)
        print(f"  [INFO] requirements written : {REQUIREMENTS_FILE}")

        result = subprocess.run(
            [self.venv_python, "-m", "venv", str(venv_dir)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"  [ERROR] venv creation failed:\n{result.stderr}")
            sys.exit(1)
        print("  [OK]   venv created")

        subprocess.run(
            [str(self.venv_mgr.pip), "install", "--quiet", "--upgrade", "pip"],
            capture_output=True,
        )

        print(f"  [INFO] Installing from requirements: {REQUIREMENTS_FILE}")
        install = subprocess.run(
            [str(self.venv_mgr.pip), "install", "-r", str(REQUIREMENTS_FILE)],
            capture_output=True,
            text=True,
        )
        if install.returncode != 0:
            print(f"  [ERROR] pip install failed:\n{install.stderr}")
            sys.exit(1)
        print("  [OK]   Packages installed")

        verify = subprocess.run(
            [
                str(self.venv_mgr.python),
                "-c",
                (
                    "import importlib.metadata as md; "
                    "import torch, torchvision, torchaudio, pytorch_benchmark; "
                    "print('torch=' + torch.__version__ + ' "
                    "torchvision=' + torchvision.__version__ + ' "
                    "torchaudio=' + torchaudio.__version__ + ' "
                    "pytorch-benchmark=' + md.version('pytorch-benchmark'))"
                ),
            ],
            capture_output=True,
            text=True,
        )
        if verify.returncode != 0:
            print(f"  [ERROR] import check failed:\n{verify.stderr}")
            sys.exit(1)
        print(f"  [OK]   {verify.stdout.strip()} ready in venv")

    def ensure_profile_present(self) -> None:
        if self.profile_dir.exists():
            return
        print(f"  [INFO] Fetching PTS profile metadata for {self.benchmark_full}")
        result = subprocess.run(
            ["phoronix-test-suite", "info", self.benchmark_full],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 or not self.profile_dir.exists():
            print(f"  [ERROR] Failed to fetch profile metadata for {self.benchmark_full}")
            if result.stderr.strip():
                print(result.stderr.strip())
            sys.exit(1)

    def install_benchmark(self) -> None:
        self.results_dir.mkdir(parents=True, exist_ok=True)
        install_log = self.results_dir / "install.log"

        install_status = get_install_status(self.benchmark_full, self.benchmark)
        already_installed = install_status["already_installed"]
        installed_dir_exists = install_status["installed_dir_exists"]

        if not already_installed and installed_dir_exists:
            print(f"  [WARN] Incomplete PTS install detected for {self.benchmark_full}, proceeding with venv setup")

        if not already_installed:
            with open(install_log, "w") as lf:
                lf.write("[INSTALL] Starting pytorch venv setup\n")

            self.ensure_profile_present()
            self.setup_venv()

            with open(install_log, "a") as lf:
                lf.write("[INSTALL] Profile metadata available\n")
                lf.write("[INSTALL] venv setup complete\n")
                lf.write("[INSTALL] Complete\n")
        else:
            print(f"  [INFO] {self.benchmark_full} already installed via PTS, skipping venv setup")

    def _workload_key(self, model: str, batch_size: int) -> str:
        return f"{self.device}-batch{batch_size}-{model}"

    def _build_benchmark_script(self, num_threads: int, model: str, batch_size: int) -> str:
        return textwrap.dedent(
            f"""
            import sys
            import torch
            import yaml
            import psutil
            from torchvision.models import {model}
            from pytorch_benchmark import benchmark

            # Workaround: psutil.cpu_freq() returns None on some ARM kernels
            # (e.g. OCI a2-flex, AWS m6g). This only affects the machine_info
            # metadata field and has no impact on benchmark timing results.
            if psutil.cpu_freq() is None:
                psutil.cpu_freq = lambda percpu=False: type(
                    "_CpuFreq", (), {{"max": 0.0, "min": 0.0, "current": 0.0}}
                )()

            device = {self.device!r}
            batch_size = {batch_size}
            num_runs = {self.internal_runs}
            num_threads = {num_threads}

            if device == "cuda" and not torch.cuda.is_available():
                print("[ERROR] CUDA requested but not available", file=sys.stderr)
                sys.exit(2)

            torch.set_num_threads(num_threads)
            model = {model}().to(device)
            model.eval()
            sample = torch.randn(2, 3, 224, 224, device=device)
            results = benchmark(
                model,
                sample,
                num_runs=num_runs,
                print_details=True,
                batch_size=batch_size,
            )
            print(yaml.dump(results, sort_keys=False))
            """
        ).strip() + "\n"

    def _run_once(self, num_threads: int, model: str, batch_size: int, run_idx: int) -> float | None:
        workload_key = self._workload_key(model, batch_size)
        safe_workload = workload_key.replace("/", "_")
        log_file = self.results_dir / f"{num_threads}threads_{safe_workload}_run{run_idx}.log"
        env = self._build_venv_env(num_threads)

        script_path = self.results_dir / f"_pytorch_benchmark_{num_threads}threads_{safe_workload}_run{run_idx}.py"
        script_path.write_text(self._build_benchmark_script(num_threads, model, batch_size))

        cmd = [str(self.venv_mgr.python), str(script_path)]
        print(f"  cmd: {' '.join(str(c) for c in cmd)}")

        with open(log_file, "w") as log_f:
            log_f.write(f"[COMMAND] {' '.join(str(c) for c in cmd)}\n")
            log_f.write(f"[CWD] {self.results_dir}\n")
            log_f.write(
                f"[CONFIG] device={self.device} batch_size={batch_size} "
                f"model={model} internal_runs={self.internal_runs} "
                f"threads={num_threads}\n\n"
            )

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
                cwd=str(self.results_dir),
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

        try:
            script_path.unlink()
        except OSError:
            pass

        pts_test_failed, pts_failure_reason = detect_pts_failure_from_log(log_file)
        if pts_test_failed:
            print(f"  [WARN] Failure marker detected in log: {pts_failure_reason}")

        if returncode == 0 and not pts_test_failed:
            for line in reversed(lines):
                m = RESULT_MEAN_RE.search(line)
                if m:
                    value = float(m.group(1))
                    print(f"  [RESULT] run{run_idx}: {workload_key} = {value:.6f} batches/sec")
                    return value
            print(f"  [WARN] No parseable result found in run{run_idx} output")
            return None

        reason = pts_failure_reason or f"returncode={returncode}"
        print(f"  [WARN] run{run_idx} failed: {reason}")
        return None

    def run_benchmark(self, num_threads: int) -> bool:
        self.results_dir.mkdir(parents=True, exist_ok=True)

        freq_start_file = self.results_dir / f"{num_threads}threads-freq_start.txt"
        freq_end_file = self.results_dir / f"{num_threads}threads-freq_end.txt"
        perf_stats_file = self.results_dir / f"{num_threads}threads-perf_stats.txt"
        perf_summary_file = self.results_dir / f"{num_threads}threads-perf_summary.json"

        self.record_cpu_frequency(freq_start_file)

        thread_results: dict[str, float] = {}
        thread_failed: list[str] = []
        for model, batch_size in self.workloads:
            workload_key = self._workload_key(model, batch_size)
            print(f"\n  >>> Workload: model={model} batch_size={batch_size} device={self.device}")
            values: list[float] = []
            for run_idx in range(1, self.times_to_run + 1):
                print(f"\n  --- Run {run_idx}/{self.times_to_run} ---")
                value = self._run_once(num_threads, model, batch_size, run_idx)
                if value is not None:
                    values.append(value)
            if values:
                mean_val = sum(values) / len(values)
                thread_results[workload_key] = mean_val
                print(f"  [SUMMARY] {workload_key}: mean = {mean_val:.6f}")
            else:
                thread_failed.append(f"{num_threads}threads:{workload_key}")
                print(f"  [FAILED] {workload_key}: all {self.times_to_run} run(s) failed")

        self.record_cpu_frequency(freq_end_file)

        if self.perf_events and perf_stats_file.exists():
            try:
                perf_summary = self.parse_perf_stats(perf_stats_file)
                with open(perf_summary_file, "w") as pf:
                    json.dump(perf_summary, pf, indent=2)
            except Exception as e:
                print(f"  [WARN] Failed to write perf summary: {e}")

        label = f"{num_threads}threads"
        if thread_results:
            self._bench_results[label] = thread_results
            self._bench_failed.extend(thread_failed)
            print(f"  [SUMMARY] {label}: {len(thread_results)}/{len(self.workloads)} workloads passed")
            return True

        self._bench_failed.extend(thread_failed or [label])
        print(f"  [FAILED] {label}: all {self.times_to_run} run(s) failed")
        return False

    def export_results(self) -> None:
        print(f"\n{'=' * 80}")
        print(">>> Exporting results")
        print(f"{'=' * 80}")

        for label, score_map in self._bench_results.items():
            num_threads = int(label.replace("threads", ""))
            json_output = self.results_dir / f"{num_threads}-thread.json"
            data = {
                "benchmark": self.benchmark,
                "test_category": self.test_category,
                "machine": self.machine_name,
                "os": self.os_name,
                "vcpu_count": self.vcpu_count,
                "num_threads": num_threads,
                "device": self.device,
                "batch_sizes": list(self.batch_sizes),
                "models": list(self.models),
                "unit": "batches/sec",
                "scale": "higher_is_better",
                "times_to_run": self.times_to_run,
                "internal_benchmark_runs": self.internal_runs,
                "results": {
                    workload_key: {
                        "value": score,
                        "unit": "batches/sec",
                    }
                    for workload_key, score in score_map.items()
                },
            }
            with open(json_output, "w") as jf:
                json.dump(data, jf, indent=2)
            print(f"  [OK] Saved: {json_output}")

        print("[OK] Export completed")

    def generate_summary(self) -> None:
        print(f"\n{'=' * 80}")
        print(">>> Generating summary")
        print(f"{'=' * 80}")

        summary_log = self.results_dir / "summary.log"
        summary_json = self.results_dir / "summary.json"

        with open(summary_log, "w") as f:
            f.write("=" * 80 + "\n")
            f.write(f"Benchmark Summary: {self.benchmark}\n")
            f.write(f"Machine  : {self.machine_name}\n")
            f.write(f"OS       : {self.os_name}\n")
            f.write(f"vCPU     : {self.vcpu_count}\n")
            f.write(f"Category : {self.test_category}\n")
            f.write(f"Device   : {self.device}\n")
            f.write(f"Batch    : {', '.join(str(v) for v in self.batch_sizes)}\n")
            f.write(f"Model    : {', '.join(self.models)}\n")
            f.write("Unit     : batches/sec (higher is better)\n")
            f.write("=" * 80 + "\n\n")
            passed = sum(len(v) for v in self._bench_results.values())
            total = len(self.thread_list) * len(self.workloads)
            f.write(f"Results ({passed}/{total} workloads passed):\n\n")
            for label, score_map in sorted(self._bench_results.items()):
                for workload_key, score in sorted(score_map.items()):
                    f.write(f"  {label:<12} {workload_key:<40} {score:<12.6f}\n")
            if self._bench_failed:
                f.write(f"\nFailed ({len(self._bench_failed)}):\n")
                for label in self._bench_failed:
                    f.write(f"  {label}\n")
        print(f"[OK] Summary log  : {summary_log}")

        summary_data = {
            "benchmark": self.benchmark,
            "test_category": self.test_category,
            "machine": self.machine_name,
            "os": self.os_name,
            "vcpu_count": self.vcpu_count,
            "device": self.device,
            "batch_sizes": list(self.batch_sizes),
            "models": list(self.models),
            "unit": "batches/sec",
            "scale": "higher_is_better",
            "results": {
                label: {
                    workload_key: {"value": score, "unit": "batches/sec"}
                    for workload_key, score in score_map.items()
                }
                for label, score_map in self._bench_results.items()
            },
            "failed": self._bench_failed,
        }
        with open(summary_json, "w") as f:
            json.dump(summary_data, f, indent=2)
        print(f"[OK] Summary JSON : {summary_json}")

    def get_os_name(self) -> str:
        try:
            r = subprocess.run(["lsb_release", "-d", "-s"], capture_output=True, text=True)
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
        try:
            if not os.path.exists("/proc/version"):
                return False
            with open("/proc/version") as f:
                content = f.read().lower()
            return "microsoft" in content or "wsl" in content
        except Exception:
            return False

    def get_cpu_frequencies(self) -> list[int]:
        frequencies: list[int] = []
        try:
            result = subprocess.run(
                ["bash", "-c", 'grep "cpu MHz" /proc/cpuinfo'],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0 and result.stdout.strip():
                for line in result.stdout.strip().split("\n"):
                    parts = line.split(":")
                    if len(parts) >= 2:
                        frequencies.append(int(float(parts[1].strip()) * 1000))
                if frequencies:
                    return frequencies
        except Exception:
            pass
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
        return frequencies

    def record_cpu_frequency(self, output_file: Path) -> bool:
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
        try:
            output_file.touch()
        except Exception:
            pass
        return False

    def parse_perf_stats(self, perf_stats_file: Path) -> dict:
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
                            metrics[parts[1]] = float(parts[0].replace(",", ""))
                        except ValueError:
                            continue
        except FileNotFoundError:
            print(f"  [INFO] perf stats file not found: {perf_stats_file}")
        except Exception as e:
            print(f"  [WARN] Failed to parse perf stats: {e}")
        return metrics

    def check_and_setup_perf_permissions(self) -> int:
        try:
            with open("/proc/sys/kernel/perf_event_paranoid") as f:
                current = int(f.read().strip())
        except Exception:
            return 2

        if current >= 1:
            result = subprocess.run(
                ["sudo", "sysctl", "-w", "kernel.perf_event_paranoid=0"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                print("  [OK]   perf_event_paranoid adjusted to 0")
                return 0
            print(f"  [WARN] perf_event_paranoid={current}; sudo adjustment failed — limited mode")
            return current
        return current

    def get_perf_events(self) -> str | None:
        perf_path = shutil.which("perf")
        if not perf_path:
            print("  [INFO] perf command not found in PATH")
            return None
        hw_events = "cycles,instructions,cpu-clock,task-clock,context-switches,cpu-migrations"
        try:
            result = subprocess.run(
                ["bash", "-c", f"{perf_path} stat -e {hw_events} sleep 0.01 2>&1"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            output = result.stdout + result.stderr
            if result.returncode == 0 and "<not supported>" not in output:
                print("  [OK]   Hardware PMU available")
                return hw_events

            sw_events = "cpu-clock,task-clock,context-switches,cpu-migrations"
            result_sw = subprocess.run(
                ["bash", "-c", f"{perf_path} stat -e {sw_events} sleep 0.01 2>&1"],
                capture_output=True,
                text=True,
                timeout=3,
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

    def run(self) -> bool:
        print(f"{'=' * 80}")
        print(f"PTS Benchmark Runner : {self.benchmark}")
        print(f"Machine              : {self.machine_name}")
        print(f"OS                   : {self.os_name}")
        print(f"vCPU count           : {self.vcpu_count}")
        print(f"Thread List          : {self.thread_list}")
        print(f"Device               : {self.device}")
        print(f"Batch Sizes          : {list(self.batch_sizes)}")
        print(f"Models               : {list(self.models)}")
        print(f"Quick mode           : {self.quick_mode}")
        print(f"Results dir          : {self.results_dir}")
        print(f"{'=' * 80}\n")

        self.results_dir.mkdir(parents=True, exist_ok=True)

        stdout_log = self.results_dir / "stdout.log"
        with open(stdout_log, "a") as f:
            f.write("=" * 80 + "\n")
            f.write("[RUNNER STARTUP]\n")
            f.write(f"Python  : {sys.version.split()[0]}\n")
            f.write(f"Machine : {self.machine_name}\n")
            f.write(f"OS      : {self.os_name}\n")
            f.write(f"vCPU    : {self.vcpu_count}\n")
            f.write(f"Threads : {self.thread_list}\n")
            f.write(f"Device  : {self.device}\n")
            f.write(f"Batch   : {list(self.batch_sizes)}\n")
            f.write(f"Model   : {list(self.models)}\n")
            f.write("=" * 80 + "\n\n")

        self.install_benchmark()

        for num_threads in self.thread_list:
            prefix = f"{num_threads}-thread"
            for f in self.results_dir.glob(f"{prefix}*"):
                if f.is_file():
                    f.unlink()
            print(f"  [INFO] Cleaned existing {prefix} results (other threads preserved)")

            print(f"\n{'=' * 80}")
            print(f">>> Running {self.benchmark} with {num_threads} thread(s)")
            print(f"{'=' * 80}")
            success = self.run_benchmark(num_threads)
            if not success:
                print(f"[ERROR] Benchmark failed for {num_threads} thread(s)")
                sys.exit(1)

        self.export_results()
        self.generate_summary()
        cleanup_pts_artifacts(self.benchmark)

        print(f"\n{'=' * 80}")
        if not self._bench_results:
            print("[ERROR] No benchmark produced a result.")
        elif self._bench_failed:
            print(
                f"[DONE] {sum(len(v) for v in self._bench_results.values())} workloads passed, "
                f"{len(self._bench_failed)} failed"
            )
        else:
            print("[SUCCESS] Benchmark completed successfully")
        print(f"{'=' * 80}")
        return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description=f"Run {BENCHMARK} benchmark (isolated venv, CPU-only matrix run)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s                 # CPU / all model+batch combinations with 4-point scaling\n"
            "  %(prog)s 288             # Run with min(288, vcpu_count) threads\n"
            "  %(prog)s --quick         # 1 outer run, reduced internal iterations"
        ),
    )
    parser.add_argument("threads_pos", nargs="?", type=int, help="Number of threads")
    parser.add_argument("--threads", type=int, help="Run only the specified thread count")
    parser.add_argument("--quick", action="store_true", help="Reduced run count for development")
    args = parser.parse_args()

    threads = args.threads if args.threads is not None else args.threads_pos
    if threads is not None and threads < 1:
        print(f"[ERROR] Thread count must be >= 1 (got: {threads})")
        sys.exit(1)
    if threads is not None:
        print("[INFO] Thread count accepted for PyTorch runtime scaling")
    if args.quick:
        print("[INFO] Quick mode enabled")

    runner = PyTorchBenchmarkRunner(
        threads_arg=threads,
        quick_mode=args.quick,
    )
    success = runner.run()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
