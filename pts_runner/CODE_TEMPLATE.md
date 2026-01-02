## コード構造テンプレート

新しいpts_runner_*.pyを作成する際は、以下のテンプレートを参考にしてください。

### 必須メソッド

```python
class BenchmarkRunner:
    def __init__(self, threads_arg=None, quick_mode=False):
        # System info
        self.vcpu_count = os.cpu_count() or 1
        self.machine_name = os.environ.get('MACHINE_NAME', os.uname().nodename)
        
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
        cmd_template = 'grep "cpu MHz" /proc/cpuinfo | awk \\'{{printf "%.0f\\\\n", $4 * 1000}}\\' > {file}'
        command = cmd_template.format(file=freq_start_file)
        subprocess.run(['bash', '-c', command], capture_output=True, text=True)
        
        # 5. Execute PTS command
        subprocess.run(['bash', '-c', pts_cmd])
        
        # 6. Record CPU frequency AFTER benchmark
        cmd_template = 'grep "cpu MHz" /proc/cpuinfo | awk \\'{{printf "%.0f\\\\n", $4 * 1000}}\\' > {file}'
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

        See README.md "PTS test profile の install.sh 修正" section for details.
        """
        # Example: Fix downloads.xml checksum (ffmpeg pattern)
        # downloads_xml = Path.home() / '.phoronix-test-suite' / 'test-profiles' / 'pts' / self.benchmark / 'downloads.xml'
        # ...apply fixes...

        # Example: Modify install.sh before installation (wrk pattern)
        # install_sh = Path.home() / '.phoronix-test-suite' / 'test-profiles' / 'pts' / self.benchmark / 'install.sh'
        # ...modify file...

        pass  # Most benchmarks don't need this

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
