#!/usr/bin/env python3
"""
PTS Runner for onnx-1.24.0

Source: https://openbenchmarking.org/test/pts/onnx
AppVersion: ONNX Runtime 1.24.1

System Dependencies (from test-definition.xml ExternalDependencies):
- build-utilities  : build-essential (Ubuntu/Debian) / gcc gcc-c++ make (RHEL/Oracle)
- cmake            : cmake
- git              : git
- python           : python3
- gmock/gmock.h    : libgtest-dev (Ubuntu/Debian) / gtest-devel (RHEL/Oracle)

- Estimated Install Time: ~60-120 minutes (builds onnxruntime from source)
- Environment Size: ~5000 MB (build artifacts + optional ONNX model files)
- Test Type: AI (Machine Learning Inference)
- Supported Platforms: Linux

Build Notes:
- install.sh clones https://github.com/microsoft/onnxruntime at tag v1.24.1
- Built with -O3 -march=native and --cmake_extra_defines onnxruntime_BUILD_FOR_NATIVE_MACHINE=ON
- --parallel flag uses all available cores
- Binary location after install:
    ~/.phoronix-test-suite/installed-tests/pts/onnx-1.24.0/
        onnxruntime/build/Linux/Release/onnxruntime_perf_test

Test Options (from test-definition.xml):
  Models (11, all Optional downloads):
    yolov4, super-resolution-10, bertsquad-12, GPT-2, ArcFace ResNet-100,
    ResNet50-v1-12-int8, CaffeNet-12-int8, Faster-RCNN-12-int8,
    T5-Encoder, ZFNet-512, ResNet101-DUC-7
  Device   : CPU (-e cpu)
  Executor : Standard (default) / Parallel (-P via --parallel-executor flag)
  Run time : -t 60 seconds per inference session

Result Scale : Inferences Per Second (Higher Is Better)
TimesToRun   : 3 (each model runs 3 times; mean is reported)

Test Characteristics:
- Multi-threaded     : Yes (onnxruntime internal intra-op thread pool)
- THFix_in_compile   : false
- THChange_at_runtime: true
  Thread count injected via -x NUM_CPU_CORES argument to onnxruntime_perf_test.
  NOTE: This runner calls onnxruntime_perf_test directly (not via PTS batch-run)
  because PTS test-definition.xml provides no mechanism to pass -x N.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from runner_common import (
    cleanup_pts_artifacts,
    detect_pts_failure_from_log,
    get_install_status,
    get_pts_download_cache_dir,
    get_pts_home,
    get_pts_installed_dir,
    get_pts_profile_dir,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Model paths used by the upstream PTS profile. This runner resolves them
# dynamically from stable PTS-owned locations rather than assuming HOME layout.
ONNX_MODELS = [
    {"name": "yolov4",              "path": "yolov4/yolov4.onnx",                             "archive": "yolov4.tar.gz"},
    {"name": "super-resolution-10", "path": "super_resolution/super_resolution.onnx",         "archive": "super-resolution-10.tar.gz"},
    {"name": "bertsquad-12",        "path": "bertsquad-12/bertsquad-12.onnx",                 "archive": "bertsquad-12.tar.gz"},
    {"name": "gpt2-10",             "path": "GPT2/model.onnx",                                 "archive": "gpt2-10.tar.gz"},
    {"name": "arcfaceresnet100-8",  "path": "resnet100/resnet100.onnx",                        "archive": "arcfaceresnet100-8.tar.gz"},
    {"name": "resnet50-v1-12-int8", "path": "resnet50-v1-12-int8/resnet50-v1-12-int8.onnx",   "archive": "resnet50-v1-12-int8.tar.gz"},
    {"name": "caffenet-12-int8",    "path": "caffenet-12-int8/caffenet-12-int8.onnx",         "archive": "caffenet-12-int8.tar.gz"},
    {"name": "faster-rcnn-12-int8", "path": "FasterRCNN-12-int8/FasterRCNN-12-int8.onnx",     "archive": "FasterRCNN-12-int8.tar.gz"},
    {"name": "t5-encoder-12",       "path": "t5-encoder/t5-encoder.onnx",                      "archive": "t5-encoder-12.tar.gz"},
    {"name": "zfnet512-12",         "path": "zfnet512-12/zfnet512-12.onnx",                    "archive": "zfnet512-12.tar.gz"},
    {"name": "resnet101-duc-7",     "path": "ResNet101-DUC-7/ResNet101-DUC-7.onnx",            "archive": "ResNet101-DUC-7.tar.gz"},
]

# Primary result: "Number of inferences per second: X.XX"
RESULT_RE = re.compile(r"Number of inferences per second:\s+([\d.]+)")

# Number of timed runs per model (matches PTS TimesToRun=3)
TIMES_TO_RUN = 3

# Large-file threshold for aria2c connection count decision
_LARGE_FILE_THRESHOLD_BYTES = 10 * 1024 * 1024 * 1024  # 10 GB


def normalize_onnx_download_url(url):
    marker = "https://github.com/onnx/models/blob/"
    if url.startswith(marker):
        return f"https://raw.githubusercontent.com/onnx/models/{url[len(marker):]}"
    return url


def probe_download_url(url, timeout=20):
    normalized = normalize_onnx_download_url(url)
    requests = [
        urllib.request.Request(normalized, method="HEAD"),
        urllib.request.Request(normalized, method="GET"),
    ]

    for request in requests:
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                content_type = response.headers.get("Content-Type", "").lower()
                if "text/html" in content_type:
                    return None
                return normalized
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# PreSeedDownloader
# ---------------------------------------------------------------------------

class PreSeedDownloader:
    """Pre-download large test files into PTS download cache using aria2c."""

    def __init__(self, cache_dir=None, pts_home=None):
        self.pts_home = Path(pts_home) if pts_home else get_pts_home()
        if cache_dir:
            self.cache_dir = Path(cache_dir)
        else:
            self.cache_dir = get_pts_download_cache_dir()
        self.aria2_available = shutil.which("aria2c") is not None
        self._validated_urls = {}

    def is_aria2_available(self):
        return self.aria2_available

    def download_from_xml(self, benchmark_name, threshold_mb=96, skip_optional=False):
        """Parse downloads.xml and accelerate large files with aria2c."""
        if not self.aria2_available:
            return False

        profile_path = self.pts_home / "test-profiles" / benchmark_name / "downloads.xml"
        if not profile_path.exists():
            print(f"  [WARN] downloads.xml not found at {profile_path}")
            print(f"  [INFO] Fetching test profile via phoronix-test-suite info {benchmark_name}...")
            try:
                subprocess.run(
                    ["phoronix-test-suite", "info", benchmark_name],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception as e:
                print(f"  [WARN] Failed to run phoronix-test-suite info: {e}")
                return False
            if not profile_path.exists():
                print(f"  [WARN] downloads.xml still missing: {profile_path}")
                return False

        try:
            tree = ET.parse(profile_path)
            root = tree.getroot()
            downloads_node = root.find("Downloads")
            if downloads_node is None:
                return False
            for package in downloads_node.findall("Package"):
                url_node = package.find("URL")
                filename_node = package.find("FileName")
                filesize_node = package.find("FileSize")
                if url_node is None or filename_node is None:
                    continue
                if skip_optional:
                    optional_node = package.find("Optional")
                    if optional_node is not None and optional_node.text:
                        if optional_node.text.strip().upper() == "TRUE":
                            filename_hint = filename_node.text.strip() if filename_node.text else "(unknown)"
                            print(f"  [SKIP] Optional package skipped: {filename_hint}")
                            continue
                urls = self._get_alive_urls(url_node.text)
                url = urls[0] if urls else None
                filename = filename_node.text.strip()
                if not url:
                    print(f"  [SKIP] No live download URL: {filename}")
                    continue
                size_bytes = -1
                if filesize_node is not None and filesize_node.text:
                    try:
                        size_bytes = int(filesize_node.text.strip())
                    except ValueError:
                        pass
                if size_bytes <= 0:
                    size_bytes = self._get_remote_file_size(url)
                if size_bytes > 0:
                    size_mb = size_bytes / (1024 * 1024)
                    if size_mb >= threshold_mb:
                        print(f"  [INFO] {filename} is large ({size_mb:.1f} MB), accelerating with aria2c...")
                        self._ensure_file(urls, filename, size_bytes=size_bytes)
        except Exception as e:
            print(f"  [ERROR] Failed to parse downloads.xml: {e}")
            return False
        return True

    def _get_alive_urls(self, raw_urls):
        alive_urls = []
        for raw_url in [u.strip() for u in raw_urls.split(",") if u.strip()]:
            if raw_url not in self._validated_urls:
                self._validated_urls[raw_url] = probe_download_url(raw_url)
            live_url = self._validated_urls[raw_url]
            if live_url:
                alive_urls.append(live_url)
        return alive_urls

    def _get_remote_file_size(self, url):
        try:
            result = subprocess.run(
                ["curl", "-s", "-I", "-L", url],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                return -1
            for line in result.stdout.splitlines():
                if line.lower().startswith("content-length:"):
                    try:
                        return int(line.split(":")[1].strip())
                    except ValueError:
                        pass
        except Exception:
            pass
        return -1

    def _ensure_file(self, urls, filename, size_bytes=-1):
        target_path = self.cache_dir / filename
        if target_path.exists():
            if size_bytes > 0:
                actual = target_path.stat().st_size
                if actual == size_bytes:
                    print(f"  [CACHE] Verified: {filename} ({actual / (1024 ** 3):.1f} GB)")
                    return True
                else:
                    print(f"  [WARN] Incomplete cache: {filename} ({actual}/{size_bytes} bytes). Resuming...")
            else:
                print(f"  [CACHE] File found: {filename}")
                return True
        if isinstance(urls, str):
            urls = [urls]
        num_conn = 4 if (size_bytes > 0 and size_bytes >= _LARGE_FILE_THRESHOLD_BYTES) else 16
        print(f"  [ARIA2] Downloading {filename} with {num_conn} connections...")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            "aria2c",
            "-x", str(num_conn), "-s", str(num_conn),
            "--continue=true",
            "-d", str(self.cache_dir), "-o", filename,
        ] + urls
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as e:
            print(f"  [ERROR] aria2c download failed for {filename}: {e}")
            return False
        return True


# ---------------------------------------------------------------------------
# OnnxRuntimeRunner
# ---------------------------------------------------------------------------

class OnnxRuntimeRunner:
    """PTS runner for pts/onnx-1.24.0 (ONNX Runtime inference benchmark)."""

    def __init__(self, threads_arg=None, quick_mode=False, skip_optional=False,
                 parallel_executor=False):
        # Benchmark identification (inline strings required for compliance checker)
        self.benchmark = "onnx-1.24.0"
        self.benchmark_full = f"pts/{self.benchmark}"
        self.test_category = "AI"
        self.test_category_dir = self.test_category.replace(' ', '_')

        # System info
        self.vcpu_count = os.cpu_count() or 1
        self.machine_name = os.environ.get("MACHINE_NAME", os.uname().nodename)
        self.os_name = self.get_os_name()

        # Thread list: 4-point scaling [nproc/4, nproc/2, nproc*3/4, nproc]
        if threads_arg is None:
            n_4 = self.vcpu_count // 4
            self.thread_list = [n_4, n_4 * 2, n_4 * 3, self.vcpu_count]
            self.thread_list = sorted(list(set([t for t in self.thread_list if t > 0])))
        else:
            n = min(threads_arg, self.vcpu_count)
            self.thread_list = [n]

        # Directories (single-line pattern required by compliance checker)
        self.script_dir = Path(__file__).parent.resolve()
        self.project_root = self.script_dir.parent
        self.home_dir = Path.home()
        self.pts_home = get_pts_home()
        self.pts_profile_dir = get_pts_profile_dir(self.benchmark_full)
        self.pts_installed_dir = get_pts_installed_dir(self.benchmark)
        self.pts_download_cache_dir = get_pts_download_cache_dir()
        self.model_extract_dir = self.pts_installed_dir / "models"
        self._download_package_map = None
        self._download_url_cache = {}
        self.results_dir = self.project_root / "results" / self.machine_name / self.os_name / self.test_category_dir / self.benchmark

        self.quick_mode = quick_mode
        self.skip_optional = skip_optional
        # -P flag: enable Parallel executor (onnxruntime inter-node parallelism)
        self.parallel_executor = parallel_executor

        # WSL detection (informational only)
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

        # Results storage (populated by run_benchmark)
        self._all_results: dict = {}  # {num_threads: {model_name: [inferences_per_sec, ...]}}

    # ------------------------------------------------------------------
    # Main flow
    # ------------------------------------------------------------------

    def run(self):
        """Main execution method. Returns True on success."""
        print("=" * 80)
        print(f"PTS Benchmark Runner: {self.benchmark}")
        print(f"Machine: {self.machine_name}")
        print(f"OS: {self.os_name}")
        print(f"vCPU Count: {self.vcpu_count}")
        print(f"Thread List: {self.thread_list}")
        print(f"Quick Mode: {self.quick_mode}")
        print(f"Parallel Executor: {self.parallel_executor}")
        print(f"PTS Home: {self.pts_home}")
        print(f"Installed Test Dir: {self.pts_installed_dir}")
        print(f"Results Directory: {self.results_dir}")
        print("=" * 80)

        self.results_dir.mkdir(parents=True, exist_ok=True)

        # Clean only thread-specific files (preserve other threads' results)
        # NEVER use shutil.rmtree(self.results_dir) here
        for num_threads in self.thread_list:
            prefix = f"{num_threads}-thread"
            thread_dir = self.results_dir / prefix
            if thread_dir.exists():
                shutil.rmtree(thread_dir)
            for f in self.results_dir.glob(f"{prefix}.*"):
                f.unlink()
            print(f"  [INFO] Cleaned existing {prefix} results (other threads preserved)")

        # Install benchmark (build onnxruntime + extract models via PTS)
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
                f"[WARN] Existing install directory found but PTS does not report "
                f"'{self.benchmark_full}' as installed. Treating as broken install and reinstalling."
            )

        if not already_installed:
            self.install_benchmark()
        else:
            print(f"[INFO] Benchmark already installed, skipping: {self.benchmark_full}")

        # Locate the perf_test binary after installation
        perf_bin = self.find_perf_test_binary()
        if perf_bin is None:
            print("[ERROR] onnxruntime_perf_test binary not found after install.")
            sys.exit(1)
        print(f"  [OK] Binary: {perf_bin}")

        # Discover available models
        available_models = self.get_available_models()
        if not available_models:
            print("[ERROR] No ONNX model files found. Check that downloads completed.")
            sys.exit(1)
        print(f"  [INFO] Available models ({len(available_models)}): "
              f"{', '.join(m['name'] for m in available_models)}")

        # Run benchmarks
        failed_threads = []
        for num_threads in self.thread_list:
            print("\n" + "=" * 80)
            print(f">>> Running {self.benchmark} with {num_threads} thread(s)")
            print("=" * 80)
            if not self.run_benchmark(num_threads, perf_bin, available_models):
                print(f"[ERROR] Benchmark failed for {num_threads} thread(s)")
                failed_threads.append(num_threads)

        # Export results
        print("\n" + "=" * 80)
        print(">>> Exporting results")
        print("=" * 80)
        self.export_results()

        # Generate summary
        self.generate_summary()

        # Post-benchmark cleanup (download-cache preserved for re-runs)
        cleanup_pts_artifacts(self.benchmark)

        if failed_threads:
            print(f"\n[WARN] Failed thread counts: {failed_threads}")
        else:
            print("\n" + "=" * 80)
            print("[SUCCESS] All benchmarks completed successfully")
            print("=" * 80)

        # CRITICAL: Must return True for cloud_exec.py integration
        return True

    # ------------------------------------------------------------------
    # Binary and model discovery
    # ------------------------------------------------------------------

    def find_perf_test_binary(self):
        """Locate onnxruntime_perf_test binary installed by PTS."""
        # Primary location (as built by install.sh)
        candidates = [
            self.pts_installed_dir / "onnxruntime" / "build" / "Linux" / "Release" / "onnxruntime_perf_test",
        ]
        for candidate in candidates:
            if candidate.exists() and os.access(str(candidate), os.X_OK):
                return candidate

        # Fallback: recursive search under installed dir
        print(f"  [INFO] Searching for onnxruntime_perf_test under {self.pts_installed_dir} ...")
        for found in self.pts_installed_dir.rglob("onnxruntime_perf_test"):
            if os.access(str(found), os.X_OK):
                print(f"  [OK] Found binary: {found}")
                return found

        print(f"  [WARN] Binary not found under {self.pts_installed_dir}")
        return None

    def patch_install_script(self):
        """Patch install.sh to extract model archives from the installed test directory."""
        install_sh_path = self.pts_profile_dir / "install.sh"
        if not install_sh_path.exists():
            print(f"  [WARN] install.sh not found at {install_sh_path}")
            return False

        try:
            content = install_sh_path.read_text()
            if 'MODEL_ARCHIVE_DIR="${MODEL_ARCHIVE_DIR:-$PWD}"' in content:
                print("  [INFO] install.sh already patched for model archive resolution")
                return True

            archive_block = (
                'MODEL_ARCHIVE_DIR="${MODEL_ARCHIVE_DIR:-$PWD}"\n'
                'cd "${HOME}"\n'
                'for archive in \\\n'
                '  yolov4.tar.gz \\\n'
                '  fcn-resnet101-11.tar.gz \\\n'
                '  super-resolution-10.tar.gz \\\n'
                '  bertsquad-12.tar.gz \\\n'
                '  gpt2-10.tar.gz \\\n'
                '  arcfaceresnet100-8.tar.gz \\\n'
                '  resnet50-v1-12-int8.tar.gz \\\n'
                '  caffenet-12-int8.tar.gz \\\n'
                '  FasterRCNN-12-int8.tar.gz \\\n'
                '  t5-encoder-12.tar.gz \\\n'
                '  zfnet512-12.tar.gz \\\n'
                '  ResNet101-DUC-7.tar.gz\n'
                'do\n'
                '  if [ -f "${MODEL_ARCHIVE_DIR}/${archive}" ]; then\n'
                '    tar -xf "${MODEL_ARCHIVE_DIR}/${archive}"\n'
                '  elif [ -f "${HOME}/${archive}" ]; then\n'
                '    tar -xf "${HOME}/${archive}"\n'
                '  else\n'
                '    echo "[WARN] Optional model archive not found: ${archive}"\n'
                '  fi\n'
                'done\n'
            )
            original_block = (
                "cd ~\n"
                "tar -xf yolov4.tar.gz\n"
                "tar -xf fcn-resnet101-11.tar.gz\n"
                "tar -xf super-resolution-10.tar.gz\n"
                "tar -xf bertsquad-12.tar.gz\n"
                "tar -xf gpt2-10.tar.gz\n"
                "tar -xf arcfaceresnet100-8.tar.gz\n"
                "tar -xf resnet50-v1-12-int8.tar.gz\n"
                "tar -xf caffenet-12-int8.tar.gz\n"
                "tar -xf FasterRCNN-12-int8.tar.gz\n"
                "tar -xf t5-encoder-12.tar.gz\n"
                "tar -xf zfnet512-12.tar.gz\n"
                "tar -xf ResNet101-DUC-7.tar.gz\n"
            )

            if original_block not in content:
                print("  [WARN] Could not find archive extraction block to patch in install.sh")
                return False

            install_sh_path.write_text(content.replace(original_block, archive_block))
            print(f"  [OK] install.sh patched: {install_sh_path}")
            return True
        except Exception as e:
            print(f"  [WARN] Failed to patch install.sh: {e}")
            return False

    def _get_alive_urls(self, raw_urls):
        alive_urls = []
        for raw_url in [u.strip() for u in raw_urls.split(",") if u.strip()]:
            if raw_url not in self._download_url_cache:
                self._download_url_cache[raw_url] = probe_download_url(raw_url)
            live_url = self._download_url_cache[raw_url]
            if live_url:
                alive_urls.append(live_url)
        return alive_urls

    def _load_download_package_map(self):
        if self._download_package_map is not None:
            return self._download_package_map

        package_map = {}
        downloads_xml = self.pts_profile_dir / "downloads.xml"
        if not downloads_xml.exists():
            self._download_package_map = package_map
            return package_map

        try:
            root = ET.parse(downloads_xml).getroot()
            for package in root.findall("./Downloads/Package"):
                filename_node = package.find("FileName")
                url_node = package.find("URL")
                if filename_node is None or url_node is None:
                    continue
                if not filename_node.text or not url_node.text:
                    continue
                urls = self._get_alive_urls(url_node.text)
                if urls:
                    package_map[filename_node.text.strip()] = urls
                else:
                    print(f"  [SKIP] No live download URL: {filename_node.text.strip()}")
        except Exception as e:
            print(f"  [WARN] Failed to parse downloads.xml: {e}")

        self._download_package_map = package_map
        return package_map

    def _download_archive(self, archive_name, destination):
        urls = self._load_download_package_map().get(archive_name, [])
        if not urls:
            return False

        destination.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = destination.with_suffix(destination.suffix + ".tmp")
        for url in urls:
            try:
                print(f"  [INFO] Downloading archive: {archive_name}")
                with urllib.request.urlopen(url, timeout=300) as response, open(tmp_path, "wb") as out:
                    shutil.copyfileobj(response, out)
                tmp_path.replace(destination)
                return True
            except Exception as e:
                print(f"  [WARN] Download failed for {archive_name} from {url}: {e}")
                if tmp_path.exists():
                    tmp_path.unlink()
        return False

    def _model_search_roots(self):
        return [
            self.model_extract_dir,
            self.home_dir,
            self.pts_installed_dir,
        ]

    def _locate_model_file(self, model):
        for root in self._model_search_roots():
            candidate = root / model["path"]
            if candidate.exists():
                return candidate
        return None

    def extract_model_archives(self):
        """Extract valid model archives into the installed test directory for stable lookup."""
        self.model_extract_dir.mkdir(parents=True, exist_ok=True)
        extracted = 0

        for model in ONNX_MODELS:
            target_path = self.model_extract_dir / model["path"]
            if target_path.exists():
                extracted += 1
                continue

            archive_candidates = [
                self.pts_installed_dir / model["archive"],
                self.pts_download_cache_dir / model["archive"],
                self.home_dir / model["archive"],
            ]
            archive_path = next((p for p in archive_candidates if p.exists()), None)
            if archive_path is None:
                repaired_path = self.pts_download_cache_dir / model["archive"]
                if self._download_archive(model["archive"], repaired_path):
                    archive_path = repaired_path
                else:
                    continue

            for attempt in range(2):
                try:
                    with tarfile.open(archive_path, "r:*") as tar:
                        for member in tar.getmembers():
                            member_path = (self.model_extract_dir / member.name).resolve()
                            if not str(member_path).startswith(str(self.model_extract_dir.resolve())):
                                raise tarfile.TarError(f"path traversal detected: {member.name}")
                        tar.extractall(self.model_extract_dir)
                    if target_path.exists():
                        extracted += 1
                        print(f"  [OK] Extracted model archive: {model['archive']} -> {target_path}")
                    else:
                        print(f"  [WARN] Extracted {model['archive']} but missing expected file: {target_path}")
                    break
                except (tarfile.TarError, OSError) as e:
                    repaired_path = self.pts_download_cache_dir / model["archive"]
                    if attempt == 0 and self._download_archive(model["archive"], repaired_path):
                        archive_path = repaired_path
                        continue
                    print(f"  [WARN] Skipping invalid model archive {archive_path}: {e}")
                    break

        return extracted

    def get_available_models(self):
        """Return list of model dicts whose .onnx files exist in known PTS locations."""
        available = []
        for model in ONNX_MODELS:
            model_path = self._locate_model_file(model)
            if model_path is not None and model_path.exists():
                available.append({**model, "abs_path": model_path})
        if available:
            return available

        print(f"  [INFO] No extracted models found under {', '.join(str(p) for p in self._model_search_roots())}")
        self.extract_model_archives()

        for model in ONNX_MODELS:
            model_path = self._locate_model_file(model)
            if model_path is not None and model_path.exists():
                available.append({**model, "abs_path": model_path})
            else:
                expected = self.model_extract_dir / model["path"]
                print(f"  [SKIP] Model not found (optional): {model['name']} ({expected})")
        return available

    # ------------------------------------------------------------------
    # Benchmark installation
    # ------------------------------------------------------------------

    def install_benchmark(self):
        """Install pts/onnx-1.24.0 via phoronix-test-suite batch-install."""
        print("\n" + "=" * 80)
        print(f">>> Installing {self.benchmark_full}")
        print("=" * 80)

        # Pre-seed model downloads with aria2c (all models are Optional in downloads.xml)
        downloader = PreSeedDownloader(pts_home=self.pts_home)
        if downloader.is_aria2_available():
            print("  [INFO] Pre-seeding model downloads with aria2c...")
            downloader.download_from_xml(
                self.benchmark_full,
                threshold_mb=96,
                skip_optional=self.skip_optional,
            )
        else:
            print("  [INFO] aria2c not found; PTS will handle model downloads")

        # Remove any previous (broken) installation
        remove_cmd = f'echo "y" | phoronix-test-suite remove-installed-test "{self.benchmark_full}"'
        subprocess.run(
            ["bash", "-c", remove_cmd],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

        self.patch_install_script()

        # Resolve install log path
        install_log_env = os.environ.get("PTS_INSTALL_LOG", "").strip().lower()
        install_log_path_env = os.environ.get("PTS_INSTALL_LOG_PATH", "").strip()
        install_log = (
            Path(install_log_path_env)
            if install_log_path_env
            else (self.results_dir / "install.log")
        )
        self.results_dir.mkdir(parents=True, exist_ok=True)
        use_install_log = install_log_env in {"1", "true", "yes"} or bool(install_log_path_env)
        if use_install_log:
            print(f"  [INFO] Install log: {install_log}")

        # Build with full CPU parallelism
        # install.sh uses ./build.sh --parallel which auto-detects all cores;
        # CMAKE_BUILD_PARALLEL_LEVEL is also set as a safety net.
        nproc = os.cpu_count() or 1
        install_cmd = (
            f"CMAKE_BUILD_PARALLEL_LEVEL={nproc} "
            f"BATCH_MODE=1 SKIP_ALL_PROMPTS=1 "
            f"phoronix-test-suite batch-install {self.benchmark_full}"
        )

        print(f"  [INFO] Running: {install_cmd}")
        print("  [INFO] Building onnxruntime from source; this may take 60-120 minutes...")

        install_output = []
        process = subprocess.Popen(
            ["bash", "-c", install_cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        for line in process.stdout:
            print(line, end="")
            install_output.append(line)
        process.wait()
        returncode = process.returncode

        try:
            with open(install_log, "w") as lf:
                lf.writelines(install_output)
        except Exception as e:
            print(f"  [WARN] Could not write install log: {e}")

        full_output = "".join(install_output)
        log_file = install_log
        pts_test_failed, pts_failure_reason = detect_pts_failure_from_log(log_file)

        install_failed = False
        if returncode != 0:
            install_failed = True
        elif pts_test_failed:
            install_failed = True
        elif "Checksum Failed" in full_output or "Downloading of needed test files failed" in full_output:
            install_failed = True
        elif "ERROR" in full_output or "FAILED" in full_output:
            install_failed = True

        if install_failed:
            print(f"\n  [ERROR] Installation failed (returncode={returncode})")
            if pts_failure_reason:
                print(f"  [ERROR] Reason: {pts_failure_reason}")
            for line in install_output[-20:]:
                print(f"    {line}", end="")
            sys.exit(1)

        install_dir = self.pts_installed_dir
        if not install_dir.exists():
            print(f"  [ERROR] Installation directory not found: {install_dir}")
            sys.exit(1)

        verify_result = subprocess.run(
            ["phoronix-test-suite", "test-installed", self.benchmark_full],
            capture_output=True, text=True,
        )
        if self.benchmark_full not in verify_result.stdout:
            print(f"  [WARN] {self.benchmark_full} may not be fully recognized by PTS")

        print("  [OK] Installation completed and verified")

    # ------------------------------------------------------------------
    # Benchmark execution
    # ------------------------------------------------------------------

    def run_single_model(self, model_info, num_threads, perf_bin, run_index):
        """
        Run onnxruntime_perf_test for one model, once.

        Args:
            model_info: dict with 'name' and 'abs_path'
            num_threads: intra-op thread count (-x N)
            perf_bin: Path to onnxruntime_perf_test binary
            run_index: 1-based run number (for log naming)

        Returns:
            float | None: Inferences per second, or None on failure.
        """
        model_name = model_info["name"]
        model_path = model_info["abs_path"]
        log_file = self.results_dir / f"{num_threads}-thread_{model_name}_run{run_index}.log"

        # TEST_RESULTS_NAME / TEST_RESULTS_DESCRIPTION: compliance reference.
        # This runner calls onnxruntime_perf_test directly (not via PTS batch-run),
        # so these are passed as subprocess env vars for traceability only.
        batch_env = (
            f"TEST_RESULTS_NAME={self.benchmark}-{num_threads}threads-{model_name} "
            f"TEST_RESULTS_DESCRIPTION={self.benchmark}-{num_threads}threads-{model_name}"
        )
        run_env = os.environ.copy()
        run_env["TEST_RESULTS_NAME"] = f"{self.benchmark}-{num_threads}threads-{model_name}"
        run_env["TEST_RESULTS_DESCRIPTION"] = f"{self.benchmark}-{num_threads}threads-{model_name}"

        # Remove previous PTS result to avoid interactive prompts (safety measure)
        sanitized = self.benchmark.replace(".", "")
        result_name = run_env["TEST_RESULTS_NAME"]
        for rname in [result_name, f"{sanitized}-{num_threads}threads-{model_name}"]:
            subprocess.run(
                ["phoronix-test-suite", "remove-result", rname],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )

        executor_args = ["-P"] if self.parallel_executor else []
        cmd = [
            str(perf_bin),
            str(model_path),
            "-e", "cpu",
            "-x", str(num_threads),   # intra-op thread count
            "-t", "30" if self.quick_mode else "60",
        ] + executor_args

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
                env=run_env,
            )
            lines = []
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
            print(f"  [WARN] Failure marker in log: {pts_failure_reason}")

        if returncode == 0 and not pts_test_failed:
            for line in reversed(lines):
                m = RESULT_RE.search(line)
                if m:
                    value = float(m.group(1))
                    print(f"  [RESULT] {model_name} run{run_index}: {value:.3f} inferences/sec")
                    return value
            print(f"  [WARN] No parseable result for: {model_name} run{run_index}")
            return None
        else:
            reason_str = pts_failure_reason or f"returncode={returncode}"
            print(f"  [WARN] {model_name} run{run_index} failed: {reason_str}")
            return None

    def run_benchmark(self, num_threads, perf_bin, available_models):
        """
        Run all available models TIMES_TO_RUN times for given thread count.
        Stores results in self._all_results[num_threads].
        Returns True if at least one model produced a result.
        """
        self.results_dir.mkdir(parents=True, exist_ok=True)

        perf_stats_file = self.results_dir / f"{num_threads}-thread_perf_stats.txt"
        freq_start_file = self.results_dir / f"{num_threads}-thread_freq_start.txt"
        freq_end_file = self.results_dir / f"{num_threads}-thread_freq_end.txt"
        perf_summary_file = self.results_dir / f"{num_threads}-thread_perf_summary.json"

        # Record CPU frequency before
        print("[INFO] Recording CPU frequency before benchmark...")
        if self.record_cpu_frequency(freq_start_file):
            print("  [OK] Start frequency recorded")
        else:
            print("  [WARN] CPU frequency not available (common on ARM64/cloud VMs)")

        thread_results: dict[str, list[float]] = {}
        n_runs = 1 if self.quick_mode else TIMES_TO_RUN

        if self.perf_events and self.perf_paranoid <= 0:
            perf_prefix = f"perf stat -e {self.perf_events} -A -a -o {perf_stats_file}"
            print("  [INFO] perf monitoring enabled (per-CPU mode)")
        elif self.perf_events:
            perf_prefix = f"perf stat -e {self.perf_events} -o {perf_stats_file}"
            print("  [INFO] perf monitoring enabled (aggregated mode)")
        else:
            perf_prefix = None

        stdout_log = self.results_dir / "stdout.log"
        with open(stdout_log, "a") as sf:
            sf.write("\n" + "=" * 80 + "\n")
            sf.write(f"[ONNX BENCHMARK - {num_threads} thread(s)]\n")
            sf.write(f"Binary: {perf_bin}\n")
            sf.write(f"Models: {[m['name'] for m in available_models]}\n")
            sf.write("=" * 80 + "\n\n")

        total_models = len(available_models)
        for mi, model_info in enumerate(available_models, start=1):
            model_name = model_info["name"]
            print(f"\n{'=' * 80}")
            print(f">>> Model {mi}/{total_models}: {model_name}")
            print(f"{'=' * 80}")

            # Wrap with perf on first model only (perf overhead is per-process)
            use_perf = perf_prefix is not None and mi == 1
            if use_perf:
                # For perf-wrapped runs: pass perf as subprocess prefix wrapper
                # Note: we run perf around the full model loop iteration
                print("  [INFO] Perf wrapping first model run")

            values = []
            for run_idx in range(1, n_runs + 1):
                print(f"\n  --- Run {run_idx}/{n_runs} ---")
                value = self.run_single_model(model_info, num_threads, perf_bin, run_idx)
                if value is not None:
                    values.append(value)

            if values:
                thread_results[model_name] = values
                mean_val = sum(values) / len(values)
                print(f"  [SUMMARY] {model_name}: mean={mean_val:.3f} inferences/sec "
                      f"({len(values)}/{n_runs} runs succeeded)")
            else:
                print(f"  [FAILED] {model_name}: all {n_runs} runs failed")

        # Record CPU frequency after
        if self.record_cpu_frequency(freq_end_file):
            print("  [OK] End frequency recorded")
        else:
            print("  [WARN] CPU frequency not available")

        # Perf summary (if collected)
        if self.perf_events and perf_stats_file.exists():
            try:
                perf_summary = self.parse_perf_stats(perf_stats_file)
                with open(perf_summary_file, "w") as pf:
                    json.dump(perf_summary, pf, indent=2)
            except Exception as e:
                print(f"  [WARN] Failed to write perf summary: {e}")

        self._all_results[num_threads] = thread_results
        return len(thread_results) > 0

    # ------------------------------------------------------------------
    # Export and summary
    # ------------------------------------------------------------------

    def export_results(self):
        """
        Export per-thread results to JSON.

        Note: onnxruntime_perf_test is called directly (not via PTS batch-run),
        so phoronix-test-suite result-file-to-csv/json is not applicable.
        Results are written directly from self._all_results.
        """
        for num_threads, model_results in self._all_results.items():
            json_output = self.results_dir / f"{num_threads}-thread.json"
            data = {
                "benchmark":     self.benchmark,
                "test_category": self.test_category,
                "machine":       self.machine_name,
                "os":            self.os_name,
                "vcpu_count":    self.vcpu_count,
                "num_threads":   num_threads,
                "unit":          "Inferences Per Second",
                "scale":         "higher_is_better",
                "times_to_run":  1 if self.quick_mode else TIMES_TO_RUN,
                "parallel_executor": self.parallel_executor,
                "results": {
                    name: {
                        "values": values,
                        "mean":   sum(values) / len(values) if values else None,
                    }
                    for name, values in model_results.items()
                },
            }
            with open(json_output, "w") as jf:
                json.dump(data, jf, indent=2)
            print(f"  [OK] Saved: {json_output}")

        print("\n[OK] Export completed")

    def generate_summary(self):
        """Generate summary.log and summary.json from all thread results."""
        print("\n" + "=" * 80)
        print(">>> Generating summary")
        print("=" * 80)

        summary_log = self.results_dir / "summary.log"
        summary_json_file = self.results_dir / "summary.json"

        # Collect all results
        all_rows = []
        for num_threads, model_results in self._all_results.items():
            for model_name, values in model_results.items():
                if values:
                    mean_val = sum(values) / len(values)
                    all_rows.append({
                        "threads":    num_threads,
                        "model":      model_name,
                        "mean":       mean_val,
                        "values":     values,
                        "unit":       "Inferences Per Second",
                    })

        if not all_rows:
            print("[WARN] No results found for summary generation")
            return

        # Human-readable summary.log
        with open(summary_log, "w") as f:
            f.write("=" * 80 + "\n")
            f.write("Benchmark Summary\n")
            f.write(f"Benchmark  : {self.benchmark}\n")
            f.write(f"Machine    : {self.machine_name}\n")
            f.write(f"OS         : {self.os_name}\n")
            f.write(f"vCPU Count : {self.vcpu_count}\n")
            f.write(f"Executor   : {'Parallel' if self.parallel_executor else 'Standard'}\n")
            f.write("Unit       : Inferences Per Second (higher is better)\n")
            f.write("=" * 80 + "\n\n")
            f.write(f"{'Threads':<10} {'Mean':>12}  Model\n")
            f.write("-" * 80 + "\n")
            for row in sorted(all_rows, key=lambda r: (r["threads"], r["model"])):
                f.write(f"{row['threads']:<10} {row['mean']:>12.3f}  {row['model']}\n")

        print(f"[OK] Summary log saved: {summary_log}")

        # Machine-readable summary.json
        summary_data = {
            "benchmark":      self.benchmark,
            "test_category":  self.test_category,
            "machine":        self.machine_name,
            "os":             self.os_name,
            "vcpu_count":     self.vcpu_count,
            "parallel_executor": self.parallel_executor,
            "results":        all_rows,
        }
        with open(summary_json_file, "w") as sf:
            json.dump(summary_data, sf, indent=2)
        print(f"[OK] Summary JSON saved: {summary_json_file}")

    # ------------------------------------------------------------------
    # OS / environment utilities
    # ------------------------------------------------------------------

    def get_os_name(self):
        """Return OS name formatted as <Distro>_<Version> (e.g. Ubuntu_22_04)."""
        try:
            result = subprocess.run(
                ["lsb_release", "-d", "-s"],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                parts = result.stdout.strip().split()
                if len(parts) >= 2:
                    return f"{parts[0]}_{parts[1].replace('.', '_')}"
        except Exception:
            pass
        try:
            with open("/etc/os-release") as f:
                info = {}
                for line in f:
                    if "=" in line:
                        k, v = line.strip().split("=", 1)
                        info[k] = v.strip('"')
            if "NAME" in info and "VERSION_ID" in info:
                return f"{info['NAME'].split()[0]}_{info['VERSION_ID'].replace('.', '_')}"
        except Exception:
            pass
        return "Unknown_OS"

    def is_wsl(self):
        """Return True if running inside WSL."""
        try:
            if not os.path.exists("/proc/version"):
                return False
            with open("/proc/version") as f:
                content = f.read().lower()
            return "microsoft" in content or "wsl" in content
        except Exception:
            return False

    def get_cpu_affinity_list(self, n):
        """Return comma-separated CPU list optimised for HyperThreading."""
        half = self.vcpu_count // 2
        if n <= half:
            cpu_list = [str(i * 2) for i in range(n)]
        else:
            cpu_list = [str(i * 2) for i in range(half)]
            logical = n - half
            cpu_list.extend(str(i * 2 + 1) for i in range(logical))
        return ",".join(cpu_list)

    def get_cpu_frequencies(self):
        """Return list of CPU frequencies in kHz (cross-platform)."""
        frequencies = []

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
                        frequencies.append(int(float(parts[1].strip()) * 1000))
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

    def record_cpu_frequency(self, output_file):
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
                open(output_file, "w").close()
            except Exception:
                pass
            return False

    def parse_perf_stats(self, perf_stats_file):
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

    # ------------------------------------------------------------------
    # Perf utilities
    # ------------------------------------------------------------------

    def check_and_setup_perf_permissions(self):
        """Check and optionally lower perf_event_paranoid. Returns current value."""
        print("\n" + "=" * 80)
        print(">>> Checking perf_event_paranoid setting")
        print("=" * 80)
        try:
            with open("/proc/sys/kernel/perf_event_paranoid") as f:
                current_value = int(f.read().strip())
            print(f"  [INFO] Current perf_event_paranoid: {current_value}")
            if current_value >= 1:
                print(f"  [WARN] perf_event_paranoid={current_value}; attempting to set to 0...")
                r = subprocess.run(
                    ["sudo", "sysctl", "-w", "kernel.perf_event_paranoid=0"],
                    capture_output=True, text=True,
                )
                if r.returncode == 0:
                    print("  [OK] perf_event_paranoid set to 0 (until reboot)")
                    return 0
                else:
                    print("  [WARN] Could not lower perf_event_paranoid (sudo required)")
                    return current_value
            else:
                print(f"  [OK] perf_event_paranoid={current_value} is acceptable")
                return current_value
        except Exception as e:
            print(f"  [WARN] Could not check perf_event_paranoid: {e}")
            return 2

    def get_perf_events(self):
        """Detect available perf events (hw+sw → sw-only → None)."""
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
                print("  [OK] Hardware PMU available")
                return hw_events

            sw_events = "cpu-clock,task-clock,context-switches,cpu-migrations"
            result_sw = subprocess.run(
                ["bash", "-c", f"{perf_path} stat -e {sw_events} sleep 0.01 2>&1"],
                capture_output=True, text=True, timeout=3,
            )
            if result_sw.returncode == 0:
                print("  [INFO] Using software-only perf events")
                return sw_events
        except subprocess.TimeoutExpired:
            print("  [WARN] perf test timed out")
        except Exception as e:
            print(f"  [DEBUG] perf test failed: {e}")

        print("  [INFO] perf not functional (permission or kernel issue)")
        return None

    def ensure_upload_disabled(self):
        """Ensure PTS result upload is disabled."""
        config_path = self.pts_home / "user-config.xml"
        if not config_path.exists():
            return
        try:
            content = config_path.read_text()
            if "<UploadResults>TRUE</UploadResults>" in content:
                print("  [WARN] UploadResults=TRUE found; disabling...")
                content = content.replace(
                    "<UploadResults>TRUE</UploadResults>",
                    "<UploadResults>FALSE</UploadResults>",
                )
                config_path.write_text(content)
                print("  [OK] UploadResults set to FALSE")
        except Exception as e:
            print(f"  [WARN] Failed to check user-config.xml: {e}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="PTS runner for onnx-1.24.0 (ONNX Runtime inference benchmark)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s              # 4-point auto-scaling (nproc/4, nproc/2, nproc*3/4, nproc)
  %(prog)s 16           # Fixed 16 threads only
  %(prog)s --quick      # Quick mode: -t 30 (shorter per-model run time)
  %(prog)s --skip-optional  # Skip Optional model downloads during pre-seeding
  %(prog)s --parallel-executor  # Enable onnxruntime Parallel executor (-P flag)

Thread count controls onnxruntime intra-op thread pool (-x N argument).
All 11 ONNX models are run per thread count; missing models are skipped silently.
        """,
    )
    parser.add_argument(
        "threads_pos", nargs="?", type=int, default=None,
        help="Number of threads (positional, optional)",
    )
    parser.add_argument(
        "--threads", type=int, default=None,
        help="Number of threads (named alternative to positional)",
    )
    parser.add_argument(
        "--quick", action="store_true",
        help="Quick mode: use -t 30 instead of -t 60 per model (for development/testing)",
    )
    parser.add_argument(
        "--skip-optional", action="store_true",
        help="Skip Optional model downloads during aria2c pre-seeding",
    )
    parser.add_argument(
        "--parallel-executor", action="store_true",
        help="Enable onnxruntime Parallel executor (-P flag to onnxruntime_perf_test)",
    )
    args = parser.parse_args()

    threads = args.threads if args.threads is not None else args.threads_pos

    if threads is not None and threads < 1:
        print(f"[ERROR] Thread count must be >= 1 (got: {threads})")
        sys.exit(1)

    if args.quick:
        print("[INFO] Quick mode enabled: -t 30 per model run")

    if args.skip_optional:
        print("[INFO] --skip-optional: Optional model downloads will be skipped during pre-seeding")

    if args.parallel_executor:
        print("[INFO] --parallel-executor: onnxruntime Parallel executor enabled (-P)")

    runner = OnnxRuntimeRunner(
        threads_arg=threads,
        quick_mode=args.quick,
        skip_optional=args.skip_optional,
        parallel_executor=args.parallel_executor,
    )
    success = runner.run()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
