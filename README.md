# Cloud one hour project
GIving about "one hour" equivalent benchmark workload for cloud instance with Phoronix Test Suite (PTS). 

## TOC

- [Benchmark preparation](#benchmark-preparation)
- [Run benchmark](#run-benchmark)
- [Analyze results](#analyze-results)
- [version history](#version-history)   

## Benchmark preparation

### Automated setup (recommended)

This will install all dependencies including GCC-14, zlib, OpenSSL, and Phoronix Test Suite.
It will also configure passwordless sudo for automated benchmark runs.

```bash
cd cloud_onehour/scripts
./prepare_tools.sh
```

**Note**: You will be asked for your sudo password once during setup. After that, sudo commands will run without password prompts.

## Run benchmark

### Simple run

```bash
# Compiler environment is automatically loaded by run_pts_benchmark.sh
./scripts/run_pts_benchmark.sh coremark-1.0.1
./scripts/run_pts_benchmark.sh openssl-3.0.1
```

### Total run
Use and test_suite.json to run multiple tests at once in batch mode.

```
```

### Tune your test_suite.json

## Analyze results

## version history

