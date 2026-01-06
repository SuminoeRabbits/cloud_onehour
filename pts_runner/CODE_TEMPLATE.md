## コード構造テンプレート

新しいpts_runner_*.pyを作成する際は、以下のテンプレートを参考にしてください。

### スクリプトヘッダ (Docstring)

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

### ユーティリティクラス (PreSeedDownloader)

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
            return False
            
        try:
            import xml.etree.ElementTree as ET
            tree = ET.parse(profile_path)
            root = tree.getroot()
            downloads_node = root.find('Downloads')
            
            if downloads_node is None:
                return False
                
            for package in downloads_node.findall('Package'):
                url = package.find('URL').text.strip()
                filename = package.find('FileName').text.strip()
                filesize_node = package.find('FileSize')
                
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
                        self.ensure_file(url, filename)
        except Exception as e:
            print(f"  [ERROR] Failed to parse downloads.xml: {e}")
            return False
        return True

    def get_remote_file_size(self, url):
        # (Implementation of curl -I logic)
        try:
            cmd = ['curl', '-s', '-I', '-L', url]
            result = subprocess.run(cmd, capture_output=True, text=True)
            for line in result.stdout.splitlines():
                if line.lower().startswith('content-length:'):
                    return int(line.split(':')[1].strip())
        except Exception:
            pass
        return -1

    def ensure_file(self, url, filename):
        target_path = self.cache_dir / filename
        if target_path.exists():
            print(f"  [CACHE] File found: {filename}")
            return True

        print(f"  [ARIA2] Downloading {filename}...")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cmd = ["aria2c", "-x", "16", "-s", "16", "-d", str(self.cache_dir), "-o", filename, url]
        subprocess.run(cmd, check=True)
        return True
```

### 必須メソッド


```python
class BenchmarkRunner:
    def __init__(self, threads_arg=None, quick_mode=False):
        # System info
        self.vcpu_count = os.cpu_count() or 1
        self.machine_name = os.environ.get('MACHINE_NAME', os.uname().nodename)
        self.os_name = self.get_os_name()
        
        # Thread list setup
        if threads_arg is None:
            self.thread_list = list(range(1, self.vcpu_count + 1))
        else:
            n = min(threads_arg, self.vcpu_count)
            self.thread_list = [n]
        
        # Quick mode for development
        self.quick_mode = quick_mode
        
        # Check perf permissions
        self.perf_paranoid = self.check_and_setup_perf_permissions()
    
    def check_and_setup_perf_permissions(self):
        """Check and adjust perf_event_paranoid setting."""
        # 実装は既存のpts_runner_build-llvm-1.6.0.pyを参照
        pass

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
    
    def run_benchmark(self, num_threads):
        """Run benchmark with specified thread count."""
        # 1. Setup environment variables
        # Quick mode: FORCE_TIMES_TO_RUN=1 for development (60-70% time reduction)
        quick_env = 'FORCE_TIMES_TO_RUN=1 ' if self.quick_mode else ''
        
        # LINUX_PERF=1: Enable PTS's built-in perf stat module (System Monitor)
        perf_env = 'LINUX_PERF=1 '
        
        batch_env = f'{quick_env}{perf_env}BATCH_MODE=1 SKIP_ALL_PROMPTS=1 DISPLAY_COMPACT_RESULTS=1 TEST_RESULTS_NAME={self.benchmark}-{num_threads}threads TEST_RESULTS_IDENTIFIER={self.benchmark}-{num_threads}threads'
        
        # 2. Build PTS command
        if num_threads >= self.vcpu_count:
            cpu_list = ','.join([str(i) for i in range(self.vcpu_count)])
            pts_base_cmd = f'phoronix-test-suite batch-run {self.benchmark_full}'
        else:
            cpu_list = self.get_cpu_affinity_list(num_threads)
            pts_base_cmd = f'taskset -c {cpu_list} phoronix-test-suite batch-run {self.benchmark_full}'
        
        # 3. Construct Final Command
        pts_cmd = f'NUM_CPU_CORES={num_threads} {batch_env} {pts_base_cmd}'
        
        # 4. Record CPU frequency BEFORE benchmark
        cmd_template = 'grep "cpu MHz" /proc/cpuinfo | awk \'{{printf "%.0f\\\\n", $4 * 1000}}\' > {file}'
        command = cmd_template.format(file=self.results_dir / f"{num_threads}-thread_freq_start.txt")
        subprocess.run(['bash', '-c', command], capture_output=True, text=True)
        
        # 5. Execute PTS command
        print(f"  [EXEC] {pts_cmd}")
        # Using run() here for simplicity in template, but Popen() with streaming is generic best practice
        result = subprocess.run(['bash', '-c', pts_cmd])
        returncode = result.returncode
        
        # 6. Record CPU frequency AFTER benchmark
        cmd_template = 'grep "cpu MHz" /proc/cpuinfo | awk \'{{printf "%.0f\\\\n", $4 * 1000}}\' > {file}'
        command = cmd_template.format(file=self.results_dir / f"{num_threads}-thread_freq_end.txt")
        subprocess.run(['bash', '-c', command], capture_output=True, text=True)
        
        if returncode != 0:
            print(f"\\n[ERROR] Benchmark failed with return code {returncode}")
            # Generate error log
            err_file = self.results_dir / f"{num_threads}-thread.err"
            with open(err_file, 'w') as f:
                f.write(f"Benchmark failed with return code {returncode}\\n")
                f.write(f"Command: {pts_cmd}\\n")
            print(f"     Error log: {err_file}")
            return False

        return True

    def install_benchmark(self):
        """Install the benchmark with optimized large file downloads."""
        print(f"\\n{'='*80}")
        print(f">>> Installing benchmark: {self.benchmark_full}")
        print(f"{'='*80}")

        # CRITICAL: Pre-download large files (>96MB) with aria2c for speed
        # This step MUST be done BEFORE phoronix-test-suite batch-install
        print(f"\\n>>> Checking for large files to pre-seed...")
        downloader = PreSeedDownloader()
        downloader.download_from_xml(self.benchmark_full, threshold_mb=96)

        # Install benchmark
        install_cmd = f'BATCH_MODE=1 SKIP_ALL_PROMPTS=1 phoronix-test-suite batch-install {self.benchmark_full}'
        print(f"\\n>>> Installing benchmark...")
        print(f"  [EXEC] {install_cmd}")

        result = subprocess.run(['bash', '-c', install_cmd], capture_output=True, text=True)

        if result.returncode != 0:
            print(f"\\n[ERROR] Benchmark installation failed")
            print(result.stdout)
            print(result.stderr)
            sys.exit(1)

        print(f"\\n[OK] Benchmark installed successfully")

    def run(self):
        """Main execution flow."""
        print(f"\\n{'#'*80}")
        print(f"# PTS Runner: {self.benchmark_full}")
        print(f"# Machine: {self.machine_name}")
        # ...
        print(f"{'#'*80}")

        # Clean results directory
        if self.results_dir.exists():
            shutil.rmtree(self.results_dir)
        self.results_dir.mkdir(parents=True, exist_ok=True)

        self.clean_pts_cache()
        self.install_benchmark()

        failed = []
        for num_threads in self.thread_list:
            if not self.run_benchmark(num_threads):
                failed.append(num_threads)

        self.export_results()
        self.generate_summary()

        print(f"\\n{'='*80}")
        print(f">>> Benchmark run completed")
        if failed:
            print(f">>> Failed thread counts: {failed}")
        print(f">>> Results directory: {self.results_dir}")
        print(f"{'='*80}")

        return len(failed) == 0

    def export_results(self):
        """Export benchmark results to CSV and JSON formats."""
        print(f"\\n{'='*80}")
        print(f">>> Exporting benchmark results")
        print(f"{'='*80}")

        pts_results_dir = Path.home() / ".phoronix-test-suite" / "test-results"

        for num_threads in self.thread_list:
            result_name = f"{self.benchmark}-{num_threads}threads"

            # Check if result exists
            result_dir = pts_results_dir / result_name
            if not result_dir.exists():
                print(f"[WARN] Result not found for {num_threads} threads: {result_dir}")
                continue

            print(f"\\n[INFO] Exporting results for {num_threads} thread(s)...")

            # Export to CSV
            csv_output = self.results_dir / f"{num_threads}-thread.csv"
            print(f"  [EXPORT] CSV: {csv_output}")
            result = subprocess.run(
                ['phoronix-test-suite', 'result-file-to-csv', result_name],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                # PTS saves to ~/result_name.csv, move it to our results directory
                home_csv = Path.home() / f"{result_name}.csv"
                if home_csv.exists():
                    shutil.move(str(home_csv), str(csv_output))
                    print(f"  [OK] Saved: {csv_output}")
            else:
                print(f"  [WARN] CSV export failed: {result.stderr}")

            # Export to JSON
            json_output = self.results_dir / f"{num_threads}-thread.json"
            print(f"  [EXPORT] JSON: {json_output}")
            result = subprocess.run(
                ['phoronix-test-suite', 'result-file-to-json', result_name],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                # PTS saves to ~/result_name.json, move it to our results directory
                home_json = Path.home() / f"{result_name}.json"
                if home_json.exists():
                    shutil.move(str(home_json), str(json_output))
                    print(f"  [OK] Saved: {json_output}")
            else:
                print(f"  [WARN] JSON export failed: {result.stderr}")

        print(f"\\n[OK] Export completed")

def main():
    parser = argparse.ArgumentParser(
        description='Benchmark Runner'
    )
    
    parser.add_argument(
        'threads',
        nargs='?',
        type=int,
        help='Number of threads (optional, omit for scaling mode)'
    )
    
    parser.add_argument(
        '--quick',
        action='store_true',
        help='Quick mode: run tests once (FORCE_TIMES_TO_RUN=1) for development'
    )
    
    args = parser.parse_args()
    
    if args.quick:
        print("[INFO] Quick mode enabled: FORCE_TIMES_TO_RUN=1")
        print("[INFO] Tests will run once instead of 3+ times (60-70%% time reduction)")
    
    # Run benchmark
    runner = BenchmarkRunner(args.threads, quick_mode=args.quick)
    success = runner.run()
    
    sys.exit(0 if success else 1)
```

### 参考実装

完全な実装例は以下を参照:
- `pts_runner_coremark-1.0.1.py` - --quickフラグ実装済み（最新）
- `pts_runner_build-llvm-1.6.0.py` - 最も完全で正しい実装
- `pts_runner_apache-3.0.0.py` - Single-threaded benchmarkの例
