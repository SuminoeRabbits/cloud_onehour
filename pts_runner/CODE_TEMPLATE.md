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
        batch_env = f'{quick_env}BATCH_MODE=1 SKIP_ALL_PROMPTS=1 DISPLAY_COMPACT_RESULTS=1 TEST_RESULTS_NAME={self.benchmark}-{num_threads}threads TEST_RESULTS_IDENTIFIER={self.benchmark}-{num_threads}threads'
        
        # 2. Build PTS command (NO environment variables in pts_base_cmd!)
        if num_threads >= self.vcpu_count:
            cpu_list = ','.join([str(i) for i in range(self.vcpu_count)])
            pts_base_cmd = f'phoronix-test-suite batch-run {self.benchmark_full}'
        else:
            cpu_list = self.get_cpu_affinity_list(num_threads)
            pts_base_cmd = f'taskset -c {cpu_list} phoronix-test-suite batch-run {self.benchmark_full}'
        
        # 3. Wrap with perf stat (environment variables BEFORE perf stat!)
        pts_cmd = f'NUM_CPU_CORES={num_threads} {batch_env} perf stat -e cycles,instructions,cpu-clock,task-clock,context-switches,cpu-migrations -A -a -o {perf_stats_file} {pts_base_cmd}'
        
        # 4. Record CPU frequency BEFORE benchmark
        cmd_template = 'grep "cpu MHz" /proc/cpuinfo | awk \'{{printf "%.0f\\\\n", $4 * 1000}}\' > {file}'
        command = cmd_template.format(file=freq_start_file)
        subprocess.run(['bash', '-c', command], capture_output=True, text=True)
        
        # 5. Execute PTS command
        subprocess.run(['bash', '-c', pts_cmd])
        
        # 6. Record CPU frequency AFTER benchmark
        cmd_template = 'grep "cpu MHz" /proc/cpuinfo | awk \'{{printf "%.0f\\\\n", $4 * 1000}}\' > {file}'
        command = cmd_template.format(file=freq_end_file)
        subprocess.run(['bash', '-c', command], capture_output=True, text=True)
        
        # 7. Parse perf stats
        perf_summary = self.parse_perf_stats_and_freq(
            perf_stats_file, freq_start_file, freq_end_file, cpu_list
        )
    
    def parse_perf_stats_and_freq(self, perf_stats_file, freq_start_file, freq_end_file, cpu_list):
        """Parse perf stat output and CPU frequency files."""
        # 実装は既存のpts_runner_build-llvm-1.6.0.pyを参照
        pass

    def clean_pts_cache(self):
        """
        Clean PTS installed tests for fresh installation.

        NOTE: This method only removes installed tests, NOT test profiles.
        Test profiles may contain manual fixes (e.g., checksum corrections)
        and should be preserved.

        Directory structure:
        - ~/.phoronix-test-suite/installed-tests/pts/<testname>/ → DELETED
        - ~/.phoronix-test-suite/test-profiles/pts/<testname>/ → PRESERVED
        """
        print(">>> Cleaning PTS cache...")

        pts_home = Path.home() / '.phoronix-test-suite'

        # NOTE: Do NOT clean test profiles - they may contain manual fixes for checksum issues
        # Only clean installed tests to force fresh compilation

        # Clean installed tests
        installed_dir = pts_home / 'installed-tests' / 'pts' / self.benchmark
        if installed_dir.exists():
            print(f"  [CLEAN] Removing installed test: {installed_dir}")
            shutil.rmtree(installed_dir)

        print("  [OK] PTS cache cleaned")

    def install_benchmark(self):
        """
        Install benchmark with error detection and verification.

        CRITICAL: PTS may return exit code 0 even when download fails.
        This method implements robust error detection.
        """
        # [Pattern 5] Pre-download large files if needed
        # (Place this logic OUTSIDE the docstring to avoid SyntaxErrors)
        # -----------------------------------------------------------------
        # print(f"\\n>>> Checking for large files to pre-seed...")
        # downloader = PreSeedDownloader()
        # if downloader.is_aria2_available():
        #     # List of large files (URL, filename)
        #     files_to_download = [
        #         {"url": "http://example.com/bigfile1.7z", "name": "bigfile1.7z"},
        #         {"url": "http://example.com/bigfile2.7z", "name": "bigfile2.7z"}
        #     ]
        #     for f in files_to_download:
        #         downloader.ensure_file(f["url"], f["name"])
        # -----------------------------------------------------------------

        print(f"\n>>> Installing {self.benchmark_full}...")


        # Remove existing installation first
        print(f"  [INFO] Removing existing installation...")
        remove_cmd = f'echo "y" | phoronix-test-suite remove-installed-test "{self.benchmark_full}"'
        subprocess.run(['bash', '-c', remove_cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Build install command
        nproc = os.cpu_count() or 1
        install_cmd = f'MAKEFLAGS="-j{nproc}" CC=gcc-14 CXX=g++-14 CFLAGS="-O3 -march=native -mtune=native" CXXFLAGS="-O3 -march=native -mtune=native" phoronix-test-suite batch-install {self.benchmark_full}'

        print(f"\n{'>'*80}")
        print(f"[PTS INSTALL COMMAND]")
        print(f"  {install_cmd}")
        print(f"{'<'*80}\n")

        # Execute install command with output capture (REQUIRED for error detection)
        result = subprocess.run(
            ['bash', '-c', install_cmd],
            capture_output=True,  # ← CRITICAL: Must capture to detect errors
            text=True
        )

        # Check for installation failure
        # PTS may return 0 even if download failed, so check stdout/stderr for error messages
        install_failed = False
        if result.returncode != 0:
            install_failed = True
        elif result.stdout and ('Checksum Failed' in result.stdout or 'Downloading of needed test files failed' in result.stdout):
            install_failed = True
        elif result.stderr and ('Checksum Failed' in result.stderr or 'failed' in result.stderr.lower()):
            install_failed = True

        if install_failed:
            print(f"  [ERROR] Installation failed")
            if result.stdout:
                print(f"  [ERROR] stdout: {result.stdout[-500:]}")  # Last 500 chars
            if result.stderr:
                print(f"  [ERROR] stderr: {result.stderr[-500:]}")
            sys.exit(1)

        # Verify installation by checking if test is actually installed
        verify_cmd = f'phoronix-test-suite info {self.benchmark_full}'
        verify_result = subprocess.run(
            ['bash', '-c', verify_cmd],
            capture_output=True,
            text=True
        )

        if verify_result.returncode != 0 or 'not found' in verify_result.stdout.lower():
            print(f"  [ERROR] Installation verification failed - test not found")
            print(f"  [INFO] This may be due to download/checksum failures")
            print(f"  [INFO] Try manually installing: phoronix-test-suite install {self.benchmark_full}")
            sys.exit(1)

        print(f"  [OK] Installation completed and verified")

    def fix_benchmark_specific_issues(self):
        """
        Fix benchmark-specific build/installation issues (optional).

        IMPORTANT: This method is OPTIONAL and only needed for special cases.
        Most benchmarks do NOT require this.

        Use cases:
        1. GCC-14 compatibility fixes (e.g., OpenSSL inline assembly errors)
        2. Checksum auto-correction (e.g., upstream dependency changes)
        3. Build option adjustments

        Implementation patterns:

        Pattern 1: Modify PTS test profile's install.sh (wrk/OpenSSL case)
        - File location: ~/.phoronix-test-suite/test-profiles/pts/<testname>/install.sh
        - Modification: Use sed to inject build options into Makefile
        - Example: apache-3.0.0, nginx-3.0.1 (wrk's OpenSSL no-asm fix)

        Pattern 2: Python-side checksum fix with retry (ffmpeg case)
        - Implement fix method in pts_runner script
        - Detect failure, apply fix, retry installation
        - Example: ffmpeg-7.0.1 (x264 checksum update)

        Pattern 3: Patching generated build files via install.sh (Java/Maven case)
        - Problem: Newer JDKs (e.g., Java 25) require explicit annotation processing
        - Fix: Patch install.sh to inject compiler flags (e.g., -proc:full) into pom.xml
        - Example: java-jmh-1.0.1 (fixes META-INF/BenchmarkList missing error)

        Pattern 4: Patching launcher scripts and data generation (Spark case)
        - Problem: Hardcoded absolute paths, missing data, runtime compatibility issues (Java 25)
        - Fix: Patch install.sh to correct paths, inject 'export' env vars into launcher scripts, and run generation scripts
        - Example: spark-1.0.1 (fixes Py4JJavaError and missing test-data)

        See README.md "PTS test profile の install.sh 修正" section for details.

        Pattern 5: Large File Download Optimization (PreSeedDownloader)
        - Problem: Large test files take too long to download via standard single-threaded wget.
        - Fix: Use generic PreSeedDownloader class to pre-download files into PTS cache using aria2c (multiconnection).
        - Example: x265-1.5.0 (2.6GB video file)
        """
        # Example: Pre-seed large file using aria2c
        # downloader = PreSeedDownloader()
        # if downloader.is_aria2_available():
        #     target_files = [
        #         {"url": "http://example.com/large-file1.7z", "name": "large-file1.7z"},
        #         {"url": "http://example.com/large-file2.7z", "name": "large-file2.7z"}
        #     ]
        #     for f in target_files:
        #         downloader.ensure_file(f["url"], f["name"])

        # Example: Fix downloads.xml checksum (ffmpeg pattern)
        # downloads_xml = Path.home() / '.phoronix-test-suite' / 'test-profiles' / 'pts' / self.benchmark / 'downloads.xml'
        # ...apply fixes...

        # Example: Modify install.sh before installation (wrk pattern)
        # install_sh = Path.home() / '.phoronix-test-suite' / 'test-profiles' / 'pts' / self.benchmark / 'install.sh'
        # ...modify file...

        pass  # Most benchmarks don't need this
    
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
            f.write("="*80 + "\n")
            f.write(f"Benchmark Summary: {self.benchmark}\n")
            f.write(f"Machine: {self.machine_name}\n")
            f.write(f"Test Category: {self.test_category}\n")
            f.write("="*80 + "\n\n")

            for result in all_results:
                f.write(f"Threads: {result['threads']}\n")
                f.write(f"  Test: {result['test_name']}\n")
                f.write(f"  Description: {result['description']}\n")
                
                # Check for None to avoid f-string crash
                if result['value'] is not None:
                    f.write(f"  Average: {result['value']:.2f} {result['unit']}\n")
                else:
                    f.write(f"  Average: None (Test Failed)\n")
                    
                # Handle raw values safely
                raw_vals = result.get('raw_values')
                if raw_vals:
                    val_str = ', '.join([f'{v:.2f}' for v in raw_vals if v is not None])
                    f.write(f"  Raw values: {val_str}\n")
                else:
                    f.write(f"  Raw values: N/A\n")
                    
                f.write("\n")

            f.write("="*80 + "\n")
            f.write("Summary Table\n")
            f.write("="*80 + "\n")
            f.write(f"{'Threads':<10} {'Average':<15} {'Unit':<20}\n")
            f.write("-"*80 + "\n")
            for result in all_results:
                val_str = f"{result['value']:.2f}" if result['value'] is not None else "None"
                f.write(f"{result['threads']:<10} {val_str:<15} {result['unit']:<20}\n")

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

    def run(self):
        """Main execution flow."""
        print(f"\\n{'#'*80}")
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
            print(f"\\n>>> Cleaning existing results directory: {self.results_dir}")
            shutil.rmtree(self.results_dir)

        self.results_dir.mkdir(parents=True, exist_ok=True)

        # Clean and install
        self.clean_pts_cache()
        self.install_benchmark()

        # Run for each thread count
        for num_threads in self.thread_list:
            self.run_benchmark(num_threads)

        # Export results to CSV and JSON
        self.export_results()

        # Generate summary
        self.generate_summary()

        print(f"\\n{'='*80}")
        print(f">>> All benchmarks completed successfully")
        print(f">>> Results directory: {self.results_dir}")
        print(f"{'='*80}")

        return True

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
