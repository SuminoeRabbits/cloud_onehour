# compress-7zip 1.9.0 - Custom Test Profile

## Purpose

This custom test profile overrides the default PTS compress-7zip-1.9.0 test profile to enable explicit multi-threading control for proper vCPU scaling benchmarks and fix GCC 14 compilation issues.

## Modifications

**File:** `install.sh`

### Change 1: Fix GCC 14 compilation error

```diff
- make -j $NUM_CPU_CORES -f makefile.gcc
+ CFLAGS="$CFLAGS -Wno-error=dangling-pointer" CXXFLAGS="$CXXFLAGS -Wno-error=dangling-pointer" make -j $NUM_CPU_CORES -f makefile.gcc
```

**Reason:** 7-Zip 22.00 has compilation errors with GCC 14 due to stricter dangling pointer checks in `LzmaEnc.c`. Adding `-Wno-error=dangling-pointer` downgrades this error to a warning.

### Change 2: Add `-mmt=$NUM_CPU_CORES` to benchmark command

```diff
- ./CPP/7zip/Bundles/Alone2/_o/7zz b > \$LOG_FILE 2>&1
+ ./CPP/7zip/Bundles/Alone2/_o/7zz b -mmt=\$NUM_CPU_CORES > \$LOG_FILE 2>&1
```

**Reason:** By default, 7-Zip's benchmark (`7zz b`) auto-detects and uses all available CPU threads. The `-mmt=N` option allows explicit control of the number of threads used, enabling proper multi-threaded scaling tests with variable thread counts.

## How It Works

1. When `run_pts_benchmark.py` executes, it sets `PTS_USER_PATH_OVERRIDE` and `NUM_CPU_CORES` environment variables
2. PTS searches for test profiles in this priority:
   - First: `user_config/test-profiles/pts/compress-7zip-1.9.0/install.sh` (this custom version)
   - Second: System default compress-7zip-1.9.0 install.sh (original)
3. PTS uses the custom `install.sh` which adds `-mmt=$NUM_CPU_CORES` to the benchmark command

## Effect

### Before (Original):
- **7-Zip benchmark:** Auto-detects all CPU threads (e.g., 8 threads on 8-core machine)
- **Result:** Always uses maximum threads, no scaling control

### After (Custom):
- **7-Zip benchmark:** Uses `-mmt=$NUM_CPU_CORES` (e.g., `-mmt=4` for 4 threads)
- **Result:** Thread count controlled by `run_pts_benchmark.py` argument

### Examples:

```bash
# Run with 1 thread
./scripts/run_pts_benchmark.py compress-7zip-1.9.0 1
# → 7zz b -mmt=1

# Run with 4 threads
./scripts/run_pts_benchmark.py compress-7zip-1.9.0 4
# → 7zz b -mmt=4

# Run with scaling (1 to max cores)
./scripts/run_pts_benchmark.py compress-7zip-1.9.0
# → 7zz b -mmt=1, 7zz b -mmt=2, ..., 7zz b -mmt=8
```

## 7-Zip `-mmt` Option

- **Syntax:** `-mmt=[on|off|{N}]`
- **Default:** `on` (auto-detect all threads)
- **Examples:**
  - `-mmt=1`: Single-threaded
  - `-mmt=4`: 4 threads
  - `-mmt=off`: Disable multi-threading (same as `-mmt=1`)

## Requirements

- Phoronix Test Suite >= 9.0.0 (for `PTS_USER_PATH_OVERRIDE` support)
- 7-Zip 22.00+ (included in pts/compress-7zip-1.9.0)

## Usage

```bash
cd /home/snakajim/work/cloud_onehour/scripts
./run_pts_benchmark.py compress-7zip-1.9.0 <threads>
```

PTS will automatically use this custom profile instead of the original.

## Notes

- Original test profile: System PTS cache (downloaded on first run)
- This override is non-destructive (original profile remains unchanged)
- Only **1 parameter added** (`-mmt=$NUM_CPU_CORES`)
- Compilation still uses all cores (`make -j $NUM_CPU_CORES`)
- This patch enables proper thread scaling for benchmarking
