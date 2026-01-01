## コード構造テンプレート

新しいpts_runner_*.pyを作成する際は、以下のテンプレートを参考にしてください。

### 必須メソッド

```python
class BenchmarkRunner:
    def __init__(self, threads_arg=None):
        # System info
        self.vcpu_count = os.cpu_count() or 1
        self.machine_name = os.environ.get('MACHINE_NAME', os.uname().nodename)
        
        # Thread list setup
        if threads_arg is None:
            self.thread_list = list(range(1, self.vcpu_count + 1))
        else:
            n = min(threads_arg, self.vcpu_count)
            self.thread_list = [n]
        
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
        batch_env = f'BATCH_MODE=1 SKIP_ALL_PROMPTS=1 DISPLAY_COMPACT_RESULTS=1 TEST_RESULTS_NAME={self.benchmark}-{num_threads}threads TEST_RESULTS_IDENTIFIER={self.benchmark}-{num_threads}threads'
        
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
```

### 参考実装

完全な実装例は以下を参照:
- `pts_runner_build-llvm-1.6.0.py` - 最も完全で正しい実装
- `pts_runner_coremark-1.0.1.py` - シンプルな実装例
- `pts_runner_apache-3.0.0.py` - Single-threaded benchmarkの例
