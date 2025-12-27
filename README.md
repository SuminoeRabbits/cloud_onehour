# Cloud one hour project
GIving about "one hour" equivalent benchmark workload for cloud instance with Phoronix Test Suite (PTS). 

## TOC

- [Benchmark preparation](#benchmark-preparation)
- [Run benchmark](#run-benchmark)
- [Analyze results](#analyze-results)
- [version history](#version-history)   

## Benchmark preparation
```
cd scripts && ./prepare_tools.sh
```

## Run benchmark

### Simple run

```bash
# Compiler environment is automatically loaded by run_pts_benchmark.sh
./scripts/run_pts_benchmark.sh coremark-1.0.1
./scripts/run_pts_benchmark.sh openssl-3.0.1
```

### Custom compiler flags

The benchmark script automatically loads optimized compiler flags from `scripts/setup_compiler_env.sh`.
To verify or customize:

```bash
# View current compiler settings
source scripts/setup_compiler_env.sh
echo "CFLAGS: $CFLAGS"

# Run benchmark (flags are auto-loaded)
./scripts/run_pts_benchmark.sh coremark-1.0.1
```

**Note**: Some benchmarks (like CoreMark) may override `-O` flags in their Makefiles.
The script attempts to pass custom flags via multiple environment variables (`XCFLAGS`, `EXTRA_CFLAGS`, etc.).

### Tune your test_suite.json

## Analyze results

## version history

