# コード構造テンプレート

新しいpts_runner_*.pyを作成する際は、以下のテンプレートを参考にしてください。

## 目次
1. [スクリプトヘッダ](#スクリプトヘッダ-docstring)
2. [ユーティリティクラス](#ユーティリティクラス-preseeddownloader)
3. [必須メソッド](#必須メソッド)
4. [環境適応型メソッド (WSL/Cloud対応)](#環境適応型メソッド-wslcloud対応)
5. [トラブルシューティングパターン](#トラブルシューティングパターン)
6. [エントリーポイント (main関数)](#エントリーポイント-main関数)
7. [参考実装](#参考実装)

---

## スクリプトヘッダ (Docstring)

スクリプトの冒頭には、対象ベンチマークの詳細情報を `phoronix-test-suite info pts/<benchmark>` コマンドから取得して記載してください。
これにより、依存関係やテスト特性（マルチスレッド対応など）が明確になります。

```python
#!/usr/bin/env python3
"""
PTS Runner for <benchmark-name>

System Dependencies (from phoronix-test-suite info):
- Software Dependencies:
  * ...
- Estimated Install Time: ...
- Environment Size: ...
- Test Type: ...
- Supported Platforms: ...

Test Characteristics:
- Multi-threaded: Yes/No
- THFix_in_compile: true/false
- THChange_at_runtime: true/false
"""
```

---

## ユーティリティクラス (PreSeedDownloader)

大規模ファイルのダウンロードを高速化するための汎用クラスです。`aria2c` が利用可能な場合に高速ダウンロードを行い、PTSのキャッシュに配置します。

```python
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

    def download_from_xml(self, benchmark_name, threshold_mb=256):
        """
        Parse downloads.xml for the benchmark and download large files.
        """
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

                # Handle comma-separated URLs
                urls = [u.strip() for u in url_node.text.split(',')]
                url = urls[0] if urls else None
                filename = filename_node.text.strip()
                
                if not url:
                    continue

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
                        # Pass all URLs to ensure_file for fallback support
                        self.ensure_file(urls, filename)
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
                print(f"  [WARN] Failed to get headers for {url}")
                return -1
                
            # Parse Content-Length
            # Look for "content-length: 12345" (case insensitive)
            for line in result.stdout.splitlines():
                if line.lower().startswith('content-length:'):
                    try:
                        size_str = line.split(':')[1].strip()
                        return int(size_str)
                    except ValueError:
                        pass
        except Exception as e:
            print(f"  [WARN] Error checking size: {e}")
            
        return -1

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

    def ensure_file(self, urls, filename):
        """
        Directly download file using aria2c (assumes size check passed).
        Args:
            urls: List of URLs or single URL string
            filename: Target filename
        """
        target_path = self.cache_dir / filename
        
        # Check if file exists in cache
        if target_path.exists():
            print(f"  [CACHE] File found: {filename}")
            return True

        if isinstance(urls, str):
            urls = [urls]

        # Need to download
        print(f"  [ARIA2] Downloading {filename} with 16 connections...")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # aria2c command - pass all URLs as separate arguments
        cmd = [
            "aria2c", "-x", "16", "-s", "16", 
            "-d", str(self.cache_dir), 
            "-o", filename
        ] + urls
        
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as e:
            print(f"  [ERROR] aria2c download failed for {filename}: {e}")
            return False
        return True
```

---

## 必須メソッド

### クラス初期化 (`__init__`)

**重要**: WSL/Cloud環境対応のため、perf機能検知を含みます。

```python
class BenchmarkRunner:
    def __init__(self, threads_arg=None, quick_mode=False):
        # Benchmark configuration (MUST SET THESE)
        self.benchmark = "benchmark-x.y.z"  # Example: "stream-1.3.4", "nginx-3.0.1"
        self.benchmark_full = f"pts/{self.benchmark}"
        self.test_category = "Category Name"  # Example: "Memory", "Cryptography and TLS"
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

        # Detect environment for logging
        self.is_wsl_env = self.is_wsl()
        if self.is_wsl_env:
            print("  [INFO] Running on WSL environment")

        # CRITICAL: Setup perf permissions BEFORE testing perf availability
        # This allows perf to work on cloud VMs with restrictive defaults (OCI, etc.)
        # Wrong order: get_perf_events() -> check_and_setup_perf_permissions() (will fail on cloud)
        # Correct order: check_and_setup_perf_permissions() -> get_perf_events() (works on cloud)
        self.perf_paranoid = self.check_and_setup_perf_permissions()

        # Feature Detection: Check if perf is actually functional
        # This MUST be called AFTER check_and_setup_perf_permissions()
        self.perf_events = self.get_perf_events()

        # Enforce safety
        self.ensure_upload_disabled()
        if self.perf_events:
            print(f"  [OK] Perf monitoring enabled with events: {self.perf_events}")
        else:
            print("  [INFO] Perf monitoring disabled (command missing or unsupported)")
```

### メインフローメソッド (`run`)

**CRITICAL**: `run()` メソッドは必ず `return True` を返す必要があります。

返り値を忘れると、`None` が返され、`main()` 関数で `sys.exit(1)` が実行され、cloud_exec.py がベンチマーク失敗と誤判定します。

```python
def run(self):
    """Main execution method."""
    print(f"{'='*80}")
    print(f"PTS Benchmark Runner: {self.benchmark}")
    print(f"Machine: {self.machine_name}")
    print(f"OS: {self.os_name}")
    print(f"vCPU Count: {self.vcpu_count}")
    print(f"Thread List: {self.thread_list}")
    print(f"Quick Mode: {self.quick_mode}")
    print(f"Results Directory: {self.results_dir}")
    print(f"{'='*80}\n")

    # Clean only thread-specific files (preserve other threads' results)
    # ⚠️ NEVER use shutil.rmtree(self.results_dir) here!
    #    When invoked per-thread (pts_runner 8, then pts_runner 12),
    #    rmtree would destroy previous threads' results.
    self.results_dir.mkdir(parents=True, exist_ok=True)
    for num_threads in self.thread_list:
        prefix = f"{num_threads}-thread"
        thread_dir = self.results_dir / prefix
        if thread_dir.exists():
            shutil.rmtree(thread_dir)
        for f in self.results_dir.glob(f"{prefix}.*"):
            f.unlink()
        print(f"  [INFO] Cleaned existing {prefix} results (other threads preserved)")

    # Install benchmark
    self.install_benchmark()

    # Run benchmark for each thread count
    for num_threads in self.thread_list:
        print(f"\n{'='*80}")
        print(f">>> Running {self.benchmark} with {num_threads} thread(s)")
        print(f"{'='*80}")

        success = self.run_benchmark(num_threads)
        if not success:
            print(f"[ERROR] Benchmark failed for {num_threads} thread(s)")
            sys.exit(1)

    # Export results
    print(f"\n{'='*80}")
    print(f">>> Exporting results")
    print(f"{'='*80}")
    self.export_results()

    # Generate summary
    self.generate_summary()

    # Post-benchmark cleanup: remove installed test to free disk space.
    # MUST be after export_results() and generate_summary() so results are
    # collected first. download-cache is preserved (re-download is expensive).
    # Errors are non-fatal ([WARN] only).
    cleanup_pts_artifacts(self.benchmark)

    print(f"\n{'='*80}")
    print(f"[SUCCESS] All benchmarks completed successfully")
    print(f"{'='*80}")

    # CRITICAL: Must return True for cloud_exec.py integration
    return True
```

### クリーンアップメソッド (`cleanup_pts_artifacts`)

`runner_common` モジュールに定義された関数。`run()` 末尾から呼ぶ。

**タイミングの保証**:
- `export_results()` と `generate_summary()` の**後**、`return` の**前**に呼ぶ
- Thread Scaling ループ (`for num_threads in self.thread_list`) の**外**なので、スレッド間での実行はない
- download-cache は削除しない（次 workload での再インストール時に利用される）

```python
# import 行に追加
from runner_common import detect_pts_failure_from_log, get_install_status, cleanup_pts_artifacts

# run() 末尾で呼び出す
self.generate_summary()
cleanup_pts_artifacts(self.benchmark)   # ← ここ
return len(failed) == 0
```

`cleanup_pts_artifacts(benchmark)` の実装は `runner_common.py` を参照。

### 基本メソッド

```python
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
```

---

## 環境適応型メソッド (WSL/Cloud対応)

### 環境検知

```python
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
```

### クロスプラットフォームCPU周波数取得（必須）

**背景**: `/proc/cpuinfo`の`cpu MHz`フィールドはx86_64でのみ利用可能。ARM64やクラウドVMでは動作しません。

```python
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
                parts = line.split(':')
                if len(parts) >= 2:
                    mhz = float(parts[1].strip())
                    frequencies.append(int(mhz * 1000))  # MHz to kHz
            if frequencies:
                return frequencies
    except Exception:
        pass

    # Method 2: /sys/devices/system/cpu/cpufreq (works on ARM64 and some x86)
    try:
        freq_files = sorted(Path('/sys/devices/system/cpu').glob('cpu[0-9]*/cpufreq/scaling_cur_freq'))
        if not freq_files:
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
```

### Perf機能検知（3段階フォールバック）

```python
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

def check_and_setup_perf_permissions(self):
    """
    Check perf_event_paranoid setting and adjust if needed.

    Returns:
        int: Current perf_event_paranoid value after adjustment
    """
    print(f"\\n{'='*80}")
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
            print(f"  [INFO] Attempting to adjust perf_event_paranoid to 0...")

            result = subprocess.run(
                ['sudo', 'sysctl', '-w', 'kernel.perf_event_paranoid=0'],
                capture_output=True,
                text=True
            )

            if result.returncode == 0:
                print(f"  [OK] perf_event_paranoid adjusted to 0 (temporary, until reboot)")
                print(f"       Per-CPU metrics and hardware counters enabled")
                print(f"       Full monitoring mode: perf stat -A -a")
                return 0
            else:
                print(f"  [ERROR] Failed to adjust perf_event_paranoid (sudo required)")
                print(f"  [WARN] Running in LIMITED mode:")
                print(f"         - No per-CPU metrics (no -A -a flags)")
                print(f"         - No hardware counters (cycles, instructions)")
                print(f"         - Software events only (aggregated)")
                print(f"         - IPC calculation not available")
                return current_value
        else:
            print(f"  [OK] perf_event_paranoid={current_value} is acceptable")
            print(f"       Full monitoring mode: perf stat -A -a")
            return current_value

    except Exception as e:
        print(f"  [ERROR] Could not check perf_event_paranoid: {e}")
        print(f"  [WARN] Assuming restrictive mode (perf_event_paranoid=2)")
        print(f"         Running in LIMITED mode without per-CPU metrics")
        return 2
```

### Perf統計パース（完全版）

詳細な実装は`pts_runner_stream-1.3.4.py`の`parse_perf_stats_and_freq()`メソッド(307-489行)を参照。

**簡略版**:
```python
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
    # 詳細な実装はpts_runner_stream-1.3.4.pyを参照

    return {'per_cpu_metrics': per_cpu_metrics, 'cpu_list': cpu_list}
```

### 環境適応型run_benchmark

**必須実装**: TEST_RESULTS_NAMEには必ず `{self.benchmark}` を使用（ハードコード禁止）

```python
def run_benchmark(self, num_threads):
    """Run benchmark with conditional perf monitoring."""
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
            print(f"  [INFO] Running with perf monitoring (per-CPU mode)")
        else:
            # Limited mode without per-CPU breakdown
            perf_cmd = f"perf stat -e {self.perf_events} -o {perf_stats_file}"
            print(f"  [INFO] Running with perf monitoring (aggregated mode)")

        pts_cmd = f'NUM_CPU_CORES={num_threads} {batch_env} {perf_cmd} {pts_base_cmd}'
    else:
        # Perf unavailable
        pts_cmd = f'NUM_CPU_CORES={num_threads} {batch_env} {pts_base_cmd}'
        print(f"  [INFO] Running without perf")

    # Record CPU frequency before benchmark (cross-platform)
    print(f"[INFO] Recording CPU frequency before benchmark...")
    if self.record_cpu_frequency(freq_start_file):
        print(f"  [OK] Start frequency recorded")
    else:
        print(f"  [WARN] CPU frequency not available (common on ARM64/cloud VMs)")

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

    # Record CPU frequency after benchmark (cross-platform)
    if self.record_cpu_frequency(freq_end_file):
        print(f"  [OK] End frequency recorded")
    else:
        print(f"  [WARN] CPU frequency not available")

    if returncode == 0:
        print(f"\n[OK] Benchmark completed successfully")
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
```

### 共通失敗判定パターン（推奨: 全runner共通）

**重要**: `returncode == 0` だけでは成功判定しないこと。PTSは失敗しても 0 を返すケースがあります。

特に以下は共通で検出対象にしてください（ベンチ固有ではない）:
- `Multiple tests are not installed`
- `The following tests failed`
- `quit with a non-zero exit status`
- `failed to properly run`

```python
pts_test_failed = False
failure_reason = ""
if log_file.exists():
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

if returncode == 0 and not pts_test_failed:
    print("[OK] Benchmark completed successfully")
else:
    print("[ERROR] Benchmark failed")
    print(f"  Reason: {failure_reason or f'returncode={returncode}'}")
    return False
```

このパターンを採用すると、「短時間で終わったのに成功扱い」の誤判定を防げます。

---

## オプション: install ログの有効化

インストール時の stdout/stderr をファイルに保存したい場合は、環境変数で有効化できます。
通常は無効（冗長回避）にしておき、必要時だけオンにする運用を想定します。

- `PTS_INSTALL_LOG=1` を設定すると `results/install.log` に保存
- `PTS_INSTALL_LOG_PATH=/path/to/file` を設定すると指定パスへ保存

**サンプル（install_benchmark内）**
```python
install_log_env = os.environ.get("PTS_INSTALL_LOG", "").strip().lower()
install_log_path = os.environ.get("PTS_INSTALL_LOG_PATH", "").strip()
use_install_log = install_log_env in {"1", "true", "yes"} or bool(install_log_path)
install_log = Path(install_log_path) if install_log_path else (self.results_dir / "install.log")
log_file = install_log
```

### install_benchmark() の必須規約（2026-02 追加）

`install_benchmark()` では、以下3点を必須とします。

1. **ログパス変数名を固定**: `log_file = install_log`
2. **`detect_pts_failure_from_log(log_file)` 呼び出し前に `log_file` を必ず定義**
3. **`pts_test_failed` を install の失敗判定に必ず含める**

```python
process.wait()
returncode = process.returncode

# MUST: define before detect call
log_file = install_log
pts_test_failed, pts_failure_reason = detect_pts_failure_from_log(log_file)

install_failed = False
if returncode != 0:
    install_failed = True
elif pts_test_failed:
    install_failed = True
elif 'Checksum Failed' in full_output or 'Downloading of needed test files failed' in full_output:
    install_failed = True
elif 'ERROR' in full_output or 'FAILED' in full_output:
    install_failed = True
```

上記により、`returncode == 0` でもPTS内部失敗を取りこぼしません。

---

## トラブルシューティングパターン

### ドット除去問題への対応

**問題**: PTSは結果ディレクトリ名からドット(`.`)を削除する
**例**: `stream-1.3.4-4threads` → `stream-134-4threads`

```python
def export_results(self):
    """Export benchmark results to CSV and JSON formats."""
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
            text=True
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
            text=True
        )
        if result.returncode == 0:
            home_json = Path.home() / f"{result_dir_name}.json"
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
        f.write(f"Benchmark Summary\n")
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
```

### コンパイラ互換性パッチ

**問題**: GCC-14でOpenSSLのインラインアセンブリがコンパイルエラー
**解決策**: install.shを動的にパッチして`no-asm`オプションを追加

```python
def patch_install_script(self):
    """
    Patch the install.sh script to add compiler compatibility fixes.

    Example: Add no-asm to OpenSSL build options for GCC-14 compatibility
    """
    install_sh_path = Path.home() / '.phoronix-test-suite' / 'test-profiles' / 'pts' / self.benchmark / 'install.sh'

    if not install_sh_path.exists():
        print(f"  [WARN] install.sh not found at {install_sh_path}")
        return False

    print(f"  [INFO] Patching install.sh for compiler compatibility...")

    try:
        with open(install_sh_path, 'r') as f:
            content = f.read()

        # Check if already patched (冪等性確保)
        if 'GCC-14 compatibility' in content or 'no-asm' in content:
            print(f"  [OK] install.sh already patched")
            return True

        # Patch example for Apache/wrk OpenSSL issue
        patch_line = '# GCC-14 compatibility: Add no-asm to OpenSSL build options\n'
        patch_line += 'sed -i \'s/OPENSSL_OPTS = no-shared no-psk no-srp no-dtls no-idea --prefix=$(abspath $(ODIR))/OPENSSL_OPTS = no-shared no-psk no-srp no-dtls no-idea no-asm --prefix=$(abspath $(ODIR))/\' Makefile\n'

        # Insert patch after 'cd wrk-4.2.0'
        if 'cd wrk-4.2.0' in content:
            patched_content = content.replace(
                'cd wrk-4.2.0\nmake -j $NUM_CPU_CORES',
                f'cd wrk-4.2.0\n{patch_line}make -j $NUM_CPU_CORES'
            )

            with open(install_sh_path, 'w') as f:
                f.write(patched_content)

            print(f"  [OK] install.sh patched successfully")
            return True
        else:
            print(f"  [WARN] Could not find patch location in install.sh")
            return False

    except Exception as e:
        print(f"  [ERROR] Failed to patch install.sh: {e}")
        return False
```

**使用方法**（install_benchmarkメソッド内）:
```python
def install_benchmark(self):
    """Install benchmark with compiler compatibility patches."""
    # Remove existing installation
    remove_cmd = f'echo "y" | phoronix-test-suite remove-installed-test "{self.benchmark_full}"'
    subprocess.run(['bash', '-c', remove_cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Patch install.sh if needed
    self.patch_install_script()

    # Build install command
    nproc = os.cpu_count() or 1
    install_cmd = f'MAKEFLAGS="-j{nproc}" CC=gcc-14 CXX=g++-14 CFLAGS="-O3 -march=native -mtune=native" CXXFLAGS="-O3 -march=native -mtune=native" phoronix-test-suite batch-install {self.benchmark_full}'

    # Execute with real-time output streaming (RECOMMENDED)
    print(f"  Running installation...")
    process = subprocess.Popen(['bash', '-c', install_cmd], 
                               stdout=subprocess.PIPE, 
                               stderr=subprocess.STDOUT,
                               text=True, 
                               bufsize=1)
    
    install_output = []
    for line in process.stdout:
        print(line, end='')
        install_output.append(line)
    
    process.wait()
    returncode = process.returncode

    # Check for installation failure (returncode + output string detection)
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
        # Show last 20 lines for quick diagnosis
        for line in install_output[-20:]:
            print(f"    {line}", end='')
        sys.exit(1)
    
    # Verify installation with dual check
    pts_home = Path.home() / '.phoronix-test-suite'
    install_dir = pts_home / 'installed-tests' / 'pts' / self.benchmark
    
    if not install_dir.exists():
        print(f"  [ERROR] Installation failed: {install_dir} does not exist")
        print(f"  [ERROR] Check output above for details")
        sys.exit(1)
    
    # Secondary check: PTS recognition
    verify_cmd = f'phoronix-test-suite test-installed {self.benchmark_full}'
    result = subprocess.run(['bash', '-c', verify_cmd], capture_output=True, text=True)
    if self.benchmark_full not in result.stdout:
        print(f"  [WARN] {self.benchmark_full} may not be fully recognized by PTS")
    
    print(f"  [OK] Installation completed and verified")
```

---

## インストール検証のベストプラクティス

**重要**: 上記の実装例では、堅牢なインストール検証パターンを採用しています。

### 従来の問題
- `subprocess.run(capture_output=True)` → インストール中の出力が見えず、問題の診断が困難
- `returncode`のみのチェック → PTSは失敗しても0を返すことがある
- `phoronix-test-suite info`による検証 → テストプロファイルの存在のみチェック、実際のインストールは未確認

### 推奨パターン（★★★★★）

1. **リアルタイム出力ストリーミング**
   ```python
   process = subprocess.Popen(['bash', '-c', install_cmd], 
                              stdout=subprocess.PIPE, 
                              stderr=subprocess.STDOUT,
                              text=True, bufsize=1)
   for line in process.stdout:
       print(line, end='')  # ユーザーに進捗を表示
   ```
   - メリット: 長時間のインストール中も進捗が見える、エラー発生時に即座に気付ける

2. **ファイルシステム検証（Primary Check）**
   ```python
   install_dir = Path.home() / '.phoronix-test-suite' / 'installed-tests' / 'pts' / self.benchmark
   if not install_dir.exists():
       print(f"  [ERROR] Installation failed: {install_dir} does not exist")
       sys.exit(1)
   ```
   - メリット: 実際のインストール結果を直接確認、PTSコマンドの不確実性を回避

3. **PTS認識確認（Secondary Check）**
   ```python
   verify_cmd = f'phoronix-test-suite test-installed {self.benchmark_full}'
   result = subprocess.run(['bash', '-c', verify_cmd], capture_output=True, text=True)
   if self.benchmark_full not in result.stdout:
       print(f"  [WARN] {self.benchmark_full} may not be fully recognized by PTS")
   ```
   - メリット: PTSとの統合問題を早期発見（警告レベル、致命的ではない）

### 効果

- **堅牢性**: ★★★★★ - 誤検知（false positive）を防止
- **デバッグ性**: ★★★★★ - 問題発生時の原因特定が容易
- **ユーザー体験**: ★★★★☆ - 進捗可視化で待ち時間の不安解消
- **メンテナンス性**: ★★★★★ - トラブルシューティング時間を大幅削減

### run()での事前インストール判定（共通化可能）

**推奨**: `already_installed` 判定は `info` / `test-installed` を優先し、
`installed-tests` ディレクトリ存在だけでは成功扱いしない。

```python
verify_result = subprocess.run(
    ['bash', '-c', f'phoronix-test-suite info {self.benchmark_full}'],
    capture_output=True,
    text=True
)
info_installed = verify_result.returncode == 0 and 'Test Installed: Yes' in verify_result.stdout

test_installed_result = subprocess.run(
    ['bash', '-c', f'phoronix-test-suite test-installed {self.benchmark_full}'],
    capture_output=True,
    text=True
)
test_installed_ok = test_installed_result.returncode == 0

installed_dir_exists = (Path.home() / '.phoronix-test-suite' / 'installed-tests' / 'pts' / self.benchmark).exists()

already_installed = info_installed or test_installed_ok
if not already_installed and installed_dir_exists:
    print("[WARN] Directory exists but PTS does not recognize install. Reinstalling.")

if not already_installed:
    self.clean_pts_cache()
    self.install_benchmark()
```

この判定は ffmpeg 固有ではなく、PTS runner 全般に適用可能です。

### 重要事項: Python 3.10 互換性

- Runner スクリプトは **Python 3.10 で実行可能** であること（必須）
- 3.11+ 専用の言語機能・標準ライブラリに依存しないこと
    - 例: `except*`, `typing.Self`, `tomllib` など
- 新規実装・修正時は、少なくとも構文レベルで Python 3.10 互換を維持すること

### 後方互換性

- 既存のrunnerスクリプトを壊すことはありません
- 新規スクリプトから段階的に導入可能
- 既存スクリプトの更新は任意（推奨ではあるが必須ではない）

---
 
## エントリーポイント (main関数)
 
以下の `main()` 関数テンプレートを使用することで、引数解析の一貫性を保つことができます。
 
**重要**: スレッド数の指定には、既存の `--threads` 名前付き引数に加え、利便性のために位置引数（Positional Argument）もサポートするようにしてください。
 
```python
def main():
    parser = argparse.ArgumentParser(
        description='<Benchmark Name> Runner',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s           # Run with 1 to vCPU threads (scaling mode)
  %(prog)s 4         # Run with 4 threads only
  %(prog)s 16        # Run with 16 threads (capped at vCPU if exceeded)
  %(prog)s --quick   # Run in quick mode
        """
    )
 
    # Positional argument for threads (Optional but Recommended)
    parser.add_argument(
        'threads_pos',
        nargs='?',
        type=int,
        help='Number of threads (optional, omit for scaling mode)'
    )
    
    # Named argument for threads (Legacy support & Explicit)
    parser.add_argument(
        '--threads',
        type=int,
        help='Run benchmark with specified number of threads only (1 to CPU count)'
    )
    
    parser.add_argument(
        '--quick',
        action='store_true',
        help='Quick mode: run tests once (FORCE_TIMES_TO_RUN=1) for development'
    )
 
    args = parser.parse_args()
    
    # Resolve threads argument (prioritize --threads if both provided, though unlikely)
    threads = args.threads if args.threads is not None else args.threads_pos
 
    if args.quick:
        print("[INFO] Quick mode enabled: FORCE_TIMES_TO_RUN=1")
        print("[INFO] Tests will run once instead of 3+ times (60-70%% time reduction)")
 
    # Validate threads argument
    if threads is not None and threads < 1:
        print(f"[ERROR] Thread count must be >= 1 (got: {threads})")
        sys.exit(1)
 
    # Run benchmark
    # MyBenchmarkRunnerは実際のクラス名に置き換えてください
    runner = MyBenchmarkRunner(threads_arg=threads, quick_mode=args.quick)
    success = runner.run()
 
    sys.exit(0 if success else 1)
 
if __name__ == "__main__":
    main()
```
 
---
 
## 参考実装

完全な実装例は以下を参照:

### 最新のベストプラクティス
- **`pts_runner_coremark-1.0.1.py`** - `--quick`フラグ実装、最新パターン
- **`pts_runner_stream-1.3.4.py`** - ドット除去対応、完全な`parse_perf_stats`実装
- **`pts_runner_apache-3.0.0.py`** - `patch_install_script`実装、シングルスレッド対応

### 機能別参考
- **Perf権限チェック**: `pts_runner_build-llvm-1.6.0.py` - 最も完全な実装
- **PreSeedDownloader**: `pts_runner_x265-1.5.0.py` - 大規模ファイル対応
- **マルチスレッド最適化**: `pts_runner_build-gcc-1.5.0.py` - CPU affinity設定

---

## テスト要件チェックリスト

新規スクリプト作成後、以下の環境でテストすること:

### 環境テスト
- [ ] Native Linux環境（perf HWイベント有効）
- [ ] WSL環境（perf導入済み）
- [ ] WSL環境（perf未導入）
- [ ] Cloud VM環境（SW events only）

### 機能テスト
- [ ] **`run()` メソッドが `return True` を返すか確認（CRITICAL）**
- [ ] `--quick`フラグで正常動作
- [ ] TEST_RESULTS_NAMEに `{self.benchmark}` を使用しているか確認
- [ ] ドット付きベンチマーク名でexport成功
- [ ] summary.json生成成功
- [ ] cloud_exec.py でベンチマーク成功と判定されるか確認
- [ ] perf無効時もエラーなく完走
- [ ] コンパイラパッチの冪等性確認
- [ ] **`cleanup_pts_artifacts(self.benchmark)` が `generate_summary()` の後に呼ばれるか確認**
- [ ] **cleanup後に `~/.phoronix-test-suite/installed-tests/pts/{benchmark}/` が削除されるか確認**
- [ ] **`download-cache/` が残ることを確認（削除されていないこと）**

### 出力ファイル確認
- [ ] `{n}-thread.log`
- [ ] `{n}-thread.csv`
- [ ] `{n}-thread.json`
- [ ] `{n}-thread_perf_summary.json`
- [ ] `summary.log`
- [ ] `summary.json`
- [ ] `stdout.log`

---

## 実装の優先順位

1. **基本構造** - `__init__`, `run`, `install_benchmark`
2. **環境適応** - `is_wsl()`, `get_perf_events()`
3. **ベンチマーク実行** - `run_benchmark()`
4. **結果エクスポート** - `export_results()` (ドット除去対応)
5. **サマリ生成** - `generate_summary()`
6. **後片付け** - `cleanup_pts_artifacts()` (runner_common から import、run() 末尾で呼ぶ)
7. **トラブルシューティング** - `patch_install_script()` (必要な場合のみ)

---

## よくある問題と解決策

### Q1: cloud_exec.pyでベンチマーク成功なのに失敗と判定される（CRITICAL）
**A**: `run()` メソッドに `return True` を追加してください。

**症状**:
- ベンチマークは正常に完了し、`[SUCCESS] All benchmarks completed successfully` が表示される
- しかし、cloud_exec.py が `[ERROR] Workload failed` と判定する
- リモートログに `FAILED` マーカーが記録される

**原因**:
`run()` メソッドが何も返さない（暗黙的に `None` を返す）ため、`main()` 関数で `sys.exit(0 if None else 1)` → `sys.exit(1)` となり、スクリプトが終了コード1で終了します。

**解決策**:
```python
def run(self):
    # ... ベンチマーク実行 ...
    print(f"[SUCCESS] All benchmarks completed successfully")

    return True  # ← これを追加
```

**検証方法**:
```bash
./pts_runner_xxx.py 1 --quick
echo "Exit code: $?"  # 0 であるべき
```

### Q2: summary.jsonが生成されない
**A**: 以下を確認:
1. TEST_RESULTS_NAMEに `{self.benchmark}` を使用しているか
2. export_results()でドット除去対応: `result_dir_name = result_name.replace('.', '')`

### Q3: WSLでperfが動かない
**A**: `get_perf_events()`が自動的にSWイベントにフォールバックします。perfなしでもベンチマークは実行可能です。

### Q4: Ubuntu 24.04でPythonパッケージのインストールに失敗する（PEP 668）
**A**: `pip3 install`に`--break-system-packages`を追加してください。

**症状**:
- インストールログに`error: externally-managed-environment`エラー
- テスト実行時に`Python unsupported`エラー
- numpyやscipyなどのパッケージがインストールされない

**原因**:
Ubuntu 24.04以降はPEP 668により、システムPythonへの直接的な`pip install`が制限されています。

**解決策 (推奨: 方法1)**:
install_benchmark()メソッド内で、PTSインストール後に手動でpipを実行:
```python
def install_benchmark(self):
    # ... PTS通常インストール ...
    
    # Manual pip install with --break-system-packages for Ubuntu 24.04+
    print(f"\n  [INFO] Installing Python dependencies with --break-system-packages...")
    pip_cmd = 'pip3 install --user --break-system-packages scipy numpy'
    pip_result = subprocess.run(['bash', '-c', pip_cmd], capture_output=True, text=True)
    
    if pip_result.returncode != 0:
        print(f"  [ERROR] pip install failed:")
        print(pip_result.stderr)
        sys.exit(1)
    else:
        print(f"  [OK] Python dependencies installed successfully")
```

**解決策 (代替: 方法2)**:
test-profiles内のinstall.shを直接編集（PTSアップデート時に上書きされるリスクあり）

**影響範囲**:
- Python依存のベンチマーク: numpy-1.2.1など
- Ubuntu 24.04以降のディストリビューション

### Q6: GCC-14でコンパイルエラー
**A**: `patch_install_script()`を実装し、`install_benchmark()`内で呼び出してください。

### Q4: perf_event_paranoidエラー
**A**: `get_perf_events()`の動作テストで自動的に判定されます。`-A -a`フラグは`perf_paranoid <= 0`の場合のみ使用されます。

### Q7: OCI環境でperf_stats.txtが生成されない（CRITICAL）

**症状**:
- OCI (Oracle Cloud Infrastructure) VMでベンチマーク実行後、`*_perf_stats.txt`が生成されない
- 他のクラウド（AWS、GCP）では正常に動作する

**原因**:
`__init__`内の初期化順序が間違っている。`get_perf_events()`が`check_and_setup_perf_permissions()`の前に呼ばれると、perf_event_paranoidの調整前にperfをテストしてしまい、失敗する。

**解決策**:
```python
# NG: 間違った順序
self.perf_events = self.get_perf_events()      # ← 先にテスト（失敗）
self.perf_paranoid = self.check_and_setup_perf_permissions()  # ← 後で調整

# OK: 正しい順序
self.perf_paranoid = self.check_and_setup_perf_permissions()  # ← 先に調整
self.perf_events = self.get_perf_events()      # ← 調整後にテスト（成功）
```

**検証方法**:
```bash
# OCI VM上で実行
./pts_runner_xxx.py 1 --quick
ls results/*/*/Compression/xxx/*perf_stats.txt  # ファイルが生成されるはず
```

### Q8: ARM64やクラウドVMでCPU周波数が取得できない

**症状**:
- `*_freq_start.txt`や`*_freq_end.txt`が空（0バイト）
- ARM64環境（GCP C4A、AWS Graviton、OCI Ampere）で発生

**原因**:
`/proc/cpuinfo`の`cpu MHz`フィールドはx86_64専用。ARM64では存在しない。

**解決策**:
`get_cpu_frequencies()`と`record_cpu_frequency()`メソッドを使用。これらは以下を順に試す:
1. `/proc/cpuinfo` (x86_64)
2. `/sys/devices/system/cpu/cpufreq/scaling_cur_freq` (ARM64、一部x86)
3. `lscpu` (フォールバック)

詳細な実装は「クロスプラットフォームCPU周波数取得」セクションを参照。
